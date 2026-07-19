#!/usr/bin/env python3
"""No-cache HTTP server and bounded API for the storage-viz dashboard."""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import http.server
import json
import os
from pathlib import Path
import posixpath
import secrets
import shutil
import socket
import socketserver
import sys
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from collector.inventory import SERVER_ID_RE, Server, load_inventory
from collector.jobs import RescanJobManager
from collector.service import PollService
from collector.store import CentralStore
from collector.transport import OpenSshTransport

VIEWER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("STORAGE_VIZ_ROOT", VIEWER_DIR.parent)).resolve()
DATA_DIR = Path(os.environ.get("STORAGE_VIZ_DATA_DIR", PROJECT_ROOT / "data")).resolve()
PORT = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STORAGE_VIZ_PORT", os.environ.get("PORT", "8088")))
BIND = os.environ.get("STORAGE_VIZ_BIND", os.environ.get("BIND", "127.0.0.1"))
TRUSTED_PROXY = os.environ.get("STORAGE_VIZ_TRUSTED_PROXY", "").lower() in {"1", "true", "yes", "on"}
IDENTITY_HEADER = os.environ.get("STORAGE_VIZ_IDENTITY_HEADER", "X-Forwarded-User")
ALLOWED_ORIGINS = frozenset(v.strip() for v in os.environ.get("STORAGE_VIZ_ALLOWED_ORIGINS", "").split(",") if v.strip())
OPERATORS = frozenset(v.strip() for v in os.environ.get("STORAGE_VIZ_OPERATOR_ALLOWLIST", "").split(",") if v.strip())
DEV_SAMPLE_DIR = os.environ.get("STORAGE_VIZ_DEV_SAMPLE_DIR", "").strip()
STATE_DIR = Path(os.environ.get("STORAGE_VIZ_STATE_DIR", PROJECT_ROOT / ".storage-viz-state")).resolve()
INVENTORY_PATH = os.environ.get("STORAGE_VIZ_INVENTORY", "").strip()
CSRF_SECRET = os.environ.get("STORAGE_VIZ_CSRF_SECRET", secrets.token_hex(32))
COOLDOWN_SECONDS = int(os.environ.get("STORAGE_VIZ_RESCAN_COOLDOWN_SECONDS", "900"))
MAX_CONCURRENT_RESCANS = int(os.environ.get("STORAGE_VIZ_RESCAN_MAX_CONCURRENT", "2"))


def _is_loopback(bind: str) -> bool:
    return bind in {"127.0.0.1", "::1", "localhost"}


if TRUSTED_PROXY and not _is_loopback(BIND):
    raise SystemExit("trusted-proxy mode requires a loopback bind")
if TRUSTED_PROXY and DEV_SAMPLE_DIR:
    raise SystemExit("dev sample mode is rejected in trusted-proxy production mode")


class _DevSampleService:
    def __init__(self, sample_dir: str | os.PathLike[str]) -> None:
        root = Path(sample_dir).resolve()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("STORAGE_VIZ_DEV_SAMPLE_DIR must be a real directory")
        self.root = root
        self.servers: tuple[Server, ...] = tuple(self._load_servers())

    def _load_servers(self) -> list[Server]:
        servers = []
        for order, path in enumerate(sorted(self.root.glob("*.sample.json"))):
            if path.is_symlink() or path.name.startswith("."):
                continue
            sid = path.name[:-len(".sample.json")]
            if not sid or not SERVER_ID_RE.fullmatch(sid) or sid in {".", ".."}:
                continue
            servers.append(Server(sid, sid, order, "localhost", 22, True, "monitoring", Path("/dev/null"), Path("/dev/null"), {"server_id": sid}, "a" * 64))
        return servers

    def _path(self, server_id: str) -> Path:
        if server_id not in {s.id for s in self.servers}:
            raise ValueError("unknown server")
        path = (self.root / f"{server_id}.sample.json").resolve()
        path.relative_to(self.root)
        return path

    def server_summaries(self) -> list[dict[str, Any]]:
        out = []
        for s in self.servers:
            snap = self.load_snapshot_for_api(s.id)
            out.append({"id": s.id, "display_name": s.display_name, "order": s.order, "snapshot_availability": "available", "freshness": "fresh", "latest_pull_status": "succeeded", "latest_scan_result": "complete", "configuration_sync": "in_sync", "mount_count": len(snap.get("mounts", [])), "active_job": None})
        return out

    def load_snapshot_for_api(self, server_id: str) -> Mapping[str, Any] | None:
        return json.loads(self._path(server_id).read_text(encoding="utf-8"))

    def load_state_for_api(self, server_id: str) -> dict[str, Any]:
        self._path(server_id)
        return {"active_job": None}


service: Any
jobs: RescanJobManager | None = None
RESCAN_API_ENABLED = False
if DEV_SAMPLE_DIR:
    service = _DevSampleService(DEV_SAMPLE_DIR)
elif INVENTORY_PATH:
    inventory = load_inventory(INVENTORY_PATH)
    service = PollService(list(inventory.servers), CentralStore(STATE_DIR), OpenSshTransport())
    jobs = RescanJobManager(service, cooldown_seconds=COOLDOWN_SECONDS, max_concurrent=MAX_CONCURRENT_RESCANS)
    RESCAN_API_ENABLED = TRUSTED_PROXY and bool(OPERATORS)
else:
    service = None


def _csrf_token(actor: str) -> str:
    return hmac.new(CSRF_SECRET.encode("utf-8"), actor.encode("utf-8"), hashlib.sha256).hexdigest()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _json(self, obj: Any, code: int = 200, *, cookie: str | None = None) -> None:
        body = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _actor(self) -> str | None:
        if not TRUSTED_PROXY:
            return "direct-viewer"
        raw = self.headers.get(IDENTITY_HEADER, "").strip()
        if not raw or len(raw) > 128 or any(c in raw for c in "\r\n/\\"):
            return None
        return raw

    def _session(self) -> tuple[int, dict[str, Any], str | None]:
        actor = self._actor()
        if actor is None:
            return 401, {"authenticated": False, "can_rescan": False}, None
        can_rescan = RESCAN_API_ENABLED and actor in OPERATORS
        token = _csrf_token(actor)
        return 200, {"authenticated": TRUSTED_PROXY, "actor": actor, "can_rescan": can_rescan, "csrf_token": token}, f"storage_viz_csrf={token}; Path=/; SameSite=Lax; HttpOnly"

    def _require_post_auth(self) -> tuple[int, dict[str, Any], str | None]:
        code, sess, _cookie = self._session()
        if code != 200:
            return code, sess, None
        if not sess.get("can_rescan"):
            return 403, {"error": "FORBIDDEN"}, None
        origin = self.headers.get("Origin", "")
        if not origin or origin not in ALLOWED_ORIGINS:
            return 403, {"error": "BAD_ORIGIN"}, None
        if self.headers.get("X-CSRF-Token", "") != sess.get("csrf_token"):
            return 403, {"error": "BAD_CSRF"}, None
        return 200, sess, str(sess["actor"])

    def _api_parts(self) -> list[str] | None:
        parsed = urlsplit(self.path)
        parts = [unquote(p) for p in parsed.path.split("/") if p]
        if not parts or parts[0] != "api":
            return None
        return parts

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0].startswith("/ai"):
            return self._json({"error": "not found"}, 404)
        parts = self._api_parts()
        if parts is not None:
            return self._handle_api_get(parts)
        route = self.path.split("?", 1)[0]
        if route == "/capabilities":
            return self._json({"rescan": False, "message": "Manual rescan only: use the bounded per-server API behind a trusted proxy."})
        if route == "/rescan-status":
            return self._json({"supported": False, "scanning": False, "message": "Legacy local rescan endpoint is disabled."})
        data_path = self._data_path()
        if data_path is not None:
            return self._serve_data_file(data_path)
        super().do_GET()

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0].startswith("/ai"):
            return self._json({"error": "not found"}, 404)
        if self.path.split("?", 1)[0] == "/rescan":
            return self._json({"supported": False, "error": "Legacy local rescan endpoint is disabled."}, 503)
        parts = self._api_parts()
        if parts == ["api", "servers"]:
            return self._json({"error": "not found"}, 404)
        if len(parts or []) == 4 and parts[1] == "servers" and parts[3] == "rescan":
            return self._handle_rescan(parts[2])
        return self._json({"error": "not found"}, 404)

    def _handle_api_get(self, parts: list[str]) -> None:
        if parts == ["api", "session"]:
            code, obj, cookie = self._session()
            return self._json(obj, code, cookie=cookie)
        if service is None:
            return self._json({"error": "api not configured"}, 503)
        if parts == ["api", "servers"]:
            return self._json({"servers": service.server_summaries()})
        if len(parts) == 4 and parts[1] == "servers" and parts[3] == "snapshot":
            try:
                snap = service.load_snapshot_for_api(parts[2])
            except Exception:
                return self._json({"error": "UNKNOWN_SERVER"}, 404)
            if snap is None:
                return self._json({"error": "SNAPSHOT_ABSENT"}, 404)
            return self._json(snap)
        if len(parts) == 4 and parts[1] == "servers" and parts[3] == "job":
            if jobs is not None:
                code, obj = jobs.job_for(parts[2]); return self._json(obj, code)
            try:
                return self._json({"job": service.load_state_for_api(parts[2]).get("active_job")})
            except Exception:
                return self._json({"error": "UNKNOWN_SERVER"}, 404)
        return self._json({"error": "not found"}, 404)

    def _handle_rescan(self, server_id: str) -> None:
        # Discard bounded bodies; no path/command/body fields influence execution.
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            return self._json({"error": "BAD_LENGTH"}, 400)
        if length < 0:
            return self._json({"error": "BAD_LENGTH"}, 400)
        if length > 4096:
            return self._json({"error": "BODY_TOO_LARGE"}, 413)
        if length:
            self.rfile.read(length)
        code, obj, actor = self._require_post_auth()
        if code != 200:
            return self._json(obj, code)
        if jobs is None:
            return self._json({"error": "RESCAN_DISABLED"}, 403)
        out_code, out = jobs.request_rescan(server_id, actor or "unknown")
        return self._json(out, out_code)

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
            self.send_error(404, "data file not found"); return
        with path.open("rb") as f:
            fs = path.stat()
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(str(path)))
            self.send_header("Content-Length", str(fs.st_size))
            self.end_headers()
            shutil.copyfileobj(f, self.wfile)

    def log_message(self, *args: Any) -> None:
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server((BIND, PORT), Handler) as httpd:
        host, port = httpd.server_address[:2]
        print(f"storage-viz serving {VIEWER_DIR} on {host}:{port}", flush=True)
        httpd.serve_forever()
