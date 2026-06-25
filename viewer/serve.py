#!/usr/bin/env python3
"""No-cache HTTP server for the storage-viz dashboard.

Default mode is safe dashboard/static-data serving with truthful capability
metadata: server-side rescan is disabled unless explicitly enabled.  Operators
can still run the scanner out-of-band (for example via systemd timer/manual sudo)
and refresh the dashboard.

Environment:
  STORAGE_VIZ_ROOT           project root (default: parent of viewer/)
  STORAGE_VIZ_DATA_DIR       JSON data directory (default: $ROOT/data)
  STORAGE_VIZ_SCANNER        scanner binary (default: $ROOT/scanner/hstscan)
  STORAGE_VIZ_SCAN_TARGETS   shell-style target list (default: / /data /data1 /data3)
  STORAGE_VIZ_HOST_ID        output basename when enabled (default: hostname)
  STORAGE_VIZ_OUTPUT         exact output JSON path when enabled
  STORAGE_VIZ_ENABLE_RESCAN  1/true/yes/on to allow POST /rescan to run scanner
  STORAGE_VIZ_RESCAN_COMMAND explicit command used by POST /rescan (also enables it)
  STORAGE_VIZ_PORT or PORT   listen port (default: 8088; CLI arg still works)
  STORAGE_VIZ_BIND or BIND   listen address (default: 0.0.0.0)
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
from pathlib import Path
import posixpath
import shlex
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit

VIEWER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("STORAGE_VIZ_ROOT", VIEWER_DIR.parent)).resolve()
DATA_DIR = Path(os.environ.get("STORAGE_VIZ_DATA_DIR", PROJECT_ROOT / "data")).resolve()
SCANNER = Path(os.environ.get("STORAGE_VIZ_SCANNER", PROJECT_ROOT / "scanner" / "hstscan")).resolve()
HOST_ID = os.environ.get("STORAGE_VIZ_HOST_ID", socket.gethostname())
DATA_FILE = Path(os.environ.get("STORAGE_VIZ_OUTPUT", DATA_DIR / f"{HOST_ID}.json")).resolve()
TARGETS = shlex.split(os.environ.get("STORAGE_VIZ_SCAN_TARGETS", "/ /data /data1 /data3"))
PORT = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STORAGE_VIZ_PORT", os.environ.get("PORT", "8088")))
BIND = os.environ.get("STORAGE_VIZ_BIND", os.environ.get("BIND", "0.0.0.0"))
RESCAN_COMMAND = os.environ.get("STORAGE_VIZ_RESCAN_COMMAND", "").strip()
ENABLE_RESCAN = os.environ.get("STORAGE_VIZ_ENABLE_RESCAN", "").lower() in {"1", "true", "yes", "on"}
RESCAN_SUPPORTED = ENABLE_RESCAN or bool(RESCAN_COMMAND)
RESCAN_MESSAGE = (
    "Server-side rescan is enabled."
    if RESCAN_SUPPORTED
    else "Manual rescan only: this server does not start scans."
)
RUN_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

state = {
    "supported": RESCAN_SUPPORTED,
    "message": RESCAN_MESSAGE,
    "scanning": False,
    "started": 0,
    "finished": 0,
    "duration": 0,
    "error": None,
    "data_file": str(DATA_FILE),
    "targets": TARGETS,
    "run_as_root": RUN_AS_ROOT,
    "scanner": str(SCANNER),
}
lock = threading.Lock()


def scan_command() -> list[str]:
    if RESCAN_COMMAND:
        return shlex.split(RESCAN_COMMAND)
    if not TARGETS:
        raise ValueError("STORAGE_VIZ_SCAN_TARGETS resolved to an empty target list")
    return [str(SCANNER), "--out", str(DATA_FILE), *TARGETS]


def run_scan() -> None:
    t0 = time.time()
    err = None
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not RESCAN_COMMAND:
            if not SCANNER.exists():
                raise FileNotFoundError(f"scanner not found: {SCANNER}")
            if not os.access(SCANNER, os.X_OK):
                raise PermissionError(f"scanner is not executable: {SCANNER}")
        result = subprocess.run(
            scan_command(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or b"").decode("utf-8", "replace")[-800:]
            err = f"scanner exited {result.returncode}: {detail}" if detail else f"scanner exited {result.returncode}"
    except Exception as exc:  # keep status endpoint responsive after failures
        err = str(exc)
    with lock:
        state.update(
            supported=RESCAN_SUPPORTED,
            message=RESCAN_MESSAGE,
            scanning=False,
            finished=time.time(),
            duration=round(time.time() - t0, 1),
            error=err,
        )


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _data_path(self) -> Path | None:
        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/data/"):
            return None
        rel = posixpath.normpath(unquote(parsed.path[len("/data/") :])).lstrip("/")
        if rel in ("", ".") or rel.startswith("../"):
            return None
        path = (DATA_DIR / rel).resolve()
        with contextlib.suppress(ValueError):
            path.relative_to(DATA_DIR)
            return path
        return None

    def _serve_data_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404, "data file not found")
            return
        try:
            f = path.open("rb")
        except OSError:
            self.send_error(404, "data file not readable")
            return
        with f:
            fs = path.stat()
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(str(path)))
            self.send_header("Content-Length", str(fs.st_size))
            self.end_headers()
            shutil.copyfileobj(f, self.wfile)

    def do_POST(self) -> None:
        route = self.path.split("?")[0]
        if route != "/rescan":
            return self._json({"error": "not found"}, 404)
        if not RESCAN_SUPPORTED:
            return self._json({"supported": False, "error": RESCAN_MESSAGE}, 503)
        with lock:
            if state["scanning"]:
                return self._json({"status": "already_running", "started": state["started"], "supported": True})
            state.update(
                supported=True,
                message=RESCAN_MESSAGE,
                scanning=True,
                started=time.time(),
                error=None,
            )
        threading.Thread(target=run_scan, daemon=True).start()
        return self._json({"status": "started", "supported": True, "data_file": str(DATA_FILE), "targets": TARGETS})

    def do_GET(self) -> None:
        route = self.path.split("?")[0]
        if route == "/rescan-status":
            with lock:
                return self._json(dict(state))
        if route == "/capabilities":
            return self._json({"rescan": RESCAN_SUPPORTED, "message": RESCAN_MESSAGE})
        data_path = self._data_path()
        if data_path is not None:
            return self._serve_data_file(data_path)
        super().do_GET()

    def log_message(self, *args) -> None:
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server((BIND, PORT), Handler) as httpd:
        host, port = httpd.server_address[:2]
        print(
            f"storage-viz serving {VIEWER_DIR} on {host}:{port} "
            f"(data={DATA_DIR}, rescan={'enabled' if RESCAN_SUPPORTED else 'manual-only'})",
            flush=True,
        )
        httpd.serve_forever()
