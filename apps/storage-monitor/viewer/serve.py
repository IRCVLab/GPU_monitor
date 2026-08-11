#!/usr/bin/env python3
"""No-cache HTTP server and bounded API for the storage-viz dashboard."""
from __future__ import annotations

import base64
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
import threading
import time
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from collector.inventory import SERVER_ID_RE, Server as InventoryServer, load_inventory

SAMPLE_FILE_TOKEN_RE = SERVER_ID_RE
from collector.jobs import RescanJobManager
from collector.service import PollService
from collector.store import CentralStore
from collector.transport import OpenSshTransport

VIEWER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("STORAGE_VIZ_ROOT", VIEWER_DIR.parent)).resolve()
DATA_DIR = Path(os.environ.get("STORAGE_VIZ_DATA_DIR", PROJECT_ROOT / "data")).resolve()
def _configured_port(*, include_argv: bool = False) -> int:
    raw = sys.argv[1] if include_argv and len(sys.argv) > 1 else os.environ.get("STORAGE_VIZ_PORT", os.environ.get("PORT", "8088"))
    return int(raw)


PORT = _configured_port()
BIND = os.environ.get("STORAGE_VIZ_BIND", os.environ.get("BIND", "127.0.0.1"))
TRUSTED_PROXY = os.environ.get("STORAGE_VIZ_TRUSTED_PROXY", "").lower() in {"1", "true", "yes", "on"}
DIRECT_LOOPBACK_RESCAN = os.environ.get("STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN", "").lower() in {"1", "true", "yes", "on"}
IDENTITY_HEADER = os.environ.get("STORAGE_VIZ_IDENTITY_HEADER", "X-Forwarded-User")
ALLOWED_ORIGINS = frozenset(v.strip() for v in os.environ.get("STORAGE_VIZ_ALLOWED_ORIGINS", "").split(",") if v.strip())
OPERATORS = frozenset(v.strip() for v in os.environ.get("STORAGE_VIZ_OPERATOR_ALLOWLIST", "").split(",") if v.strip())
DEV_SAMPLE_DIR = os.environ.get("STORAGE_VIZ_DEV_SAMPLE_DIR", "").strip()
STATE_DIR = Path(os.environ.get("STORAGE_VIZ_STATE_DIR", PROJECT_ROOT / ".storage-viz-state")).resolve()
INVENTORY_PATH = os.environ.get("STORAGE_VIZ_INVENTORY", "").strip()
CSRF_SECRET = os.environ.get("STORAGE_VIZ_CSRF_SECRET", secrets.token_hex(32))
SESSION_TTL_SECONDS = int(os.environ.get("STORAGE_VIZ_SESSION_TTL_SECONDS", "3600"))
COOLDOWN_SECONDS = int(os.environ.get("STORAGE_VIZ_RESCAN_COOLDOWN_SECONDS", "900"))
MAX_CONCURRENT_RESCANS = int(os.environ.get("STORAGE_VIZ_RESCAN_MAX_CONCURRENT", "2"))


def _is_loopback(bind: str) -> bool:
    return bind in {"127.0.0.1", "::1", "localhost"}


def _is_loopback_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        and parsed.username is None
        and parsed.password is None
        and not parsed.netloc.endswith(":")
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
    )


if TRUSTED_PROXY and not _is_loopback(BIND):
    raise SystemExit("trusted-proxy mode requires a loopback bind")
if TRUSTED_PROXY and DEV_SAMPLE_DIR:
    raise SystemExit("dev sample mode is rejected in trusted-proxy production mode")
if TRUSTED_PROXY and OPERATORS and not ALLOWED_ORIGINS:
    raise SystemExit("trusted-proxy operator mode requires at least one exact allowed origin")
if DIRECT_LOOPBACK_RESCAN:
    if TRUSTED_PROXY:
        raise SystemExit("direct loopback rescan mode cannot be combined with trusted-proxy mode")
    if not _is_loopback(BIND):
        raise SystemExit("direct loopback rescan mode requires a loopback bind")
    if DEV_SAMPLE_DIR:
        raise SystemExit("direct loopback rescan mode is rejected in dev sample mode")
    if not INVENTORY_PATH:
        raise SystemExit("direct loopback rescan mode requires production inventory")
    if "direct-viewer" not in OPERATORS:
        raise SystemExit("direct loopback rescan mode requires direct-viewer in operator allowlist")
    if not ALLOWED_ORIGINS:
        raise SystemExit("direct loopback rescan mode requires at least one exact allowed origin")
    if not all(_is_loopback_origin(origin) for origin in ALLOWED_ORIGINS):
        raise SystemExit("direct loopback rescan mode accepts loopback HTTP origins only")


class _DevSampleService:
    data_mode = "sample"

    def __init__(self, sample_dir: str | os.PathLike[str]) -> None:
        raw_root = Path(sample_dir)
        if raw_root.is_symlink():
            raise ValueError("STORAGE_VIZ_DEV_SAMPLE_DIR must be a real directory")
        root = raw_root.resolve()
        if not root.is_dir():
            raise ValueError("STORAGE_VIZ_DEV_SAMPLE_DIR must be a real directory")
        self.root = root
        self._paths: dict[str, Path] = {}
        self.servers: tuple[InventoryServer, ...] = tuple(self._load_servers())

    def _safe_manifest_path(self, file_token: str) -> Path:
        if not isinstance(file_token, str) or not SAMPLE_FILE_TOKEN_RE.fullmatch(file_token) or file_token in {".", ".."}:
            raise ValueError("sample manifest file token is unsafe")
        candidate = self.root / f"{file_token}.sample.json"
        if candidate.is_symlink():
            raise ValueError("sample manifest file is missing or unsafe")
        path = candidate.resolve(strict=False)
        path.relative_to(self.root)
        if path.is_symlink() or not path.is_file():
            raise ValueError("sample manifest file is missing or unsafe")
        return path

    def _load_manifest(self) -> list[dict[str, Any]]:
        manifest_candidate = self.root / "hosts.json"
        if manifest_candidate.is_symlink():
            raise ValueError("DEV_SAMPLE_DIR requires hosts.json manifest")
        manifest_path = manifest_candidate.resolve(strict=False)
        manifest_path.relative_to(self.root)
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("DEV_SAMPLE_DIR requires hosts.json manifest")
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("sample manifest must be a non-empty array")
        return raw

    def _load_servers(self) -> list[InventoryServer]:
        rows = self._load_manifest()
        servers: list[InventoryServer] = []
        seen_ids: set[str] = set()
        seen_files: set[str] = set()
        default_count = 0
        listed_files: set[str] = set()
        for order, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError("sample manifest rows must be objects")
            unknown_keys = set(row) - {"id", "label", "file", "default", "sample_data", "description"}
            if unknown_keys:
                raise ValueError("sample manifest row has unknown keys")
            sid = row.get("id")
            file_token = row.get("file")
            label = row.get("label", sid)
            if not isinstance(sid, str) or not SERVER_ID_RE.fullmatch(sid) or sid in {".", ".."}:
                raise ValueError("sample manifest id is unsafe")
            if sid in seen_ids:
                raise ValueError("duplicate sample server id")
            if not isinstance(file_token, str) or not SAMPLE_FILE_TOKEN_RE.fullmatch(file_token) or file_token in {".", ".."}:
                raise ValueError("sample manifest file token is unsafe")
            if file_token in seen_files:
                raise ValueError("duplicate sample file")
            if row.get("default") is True:
                default_count += 1
            if row.get("sample_data") is not True:
                raise ValueError("sample manifest rows require sample_data true")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("sample manifest label is required")
            path = self._safe_manifest_path(file_token)
            seen_ids.add(sid)
            seen_files.add(file_token)
            listed_files.add(path.name)
            self._paths[sid] = path
            servers.append(InventoryServer(sid, label, order, "localhost", 22, True, "monitoring", Path("/dev/null"), Path("/dev/null"), {"server_id": sid}, "a" * 64))
        if default_count != 1:
            raise ValueError("sample manifest requires exactly one default")
        present_files = {p.name for p in self.root.iterdir() if p.name.endswith(".sample.json")}
        if present_files != listed_files:
            raise ValueError("sample files must exactly match hosts.json")
        return servers

    def _path(self, server_id: str) -> Path:
        try:
            path = self._paths[server_id]
        except KeyError as exc:
            raise ValueError("unknown server") from exc
        resolved = path.resolve(strict=True)
        resolved.relative_to(self.root)
        if resolved.is_symlink() or not resolved.is_file():
            raise ValueError("sample file is missing or unsafe")
        return resolved

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
    service.data_mode = "inventory"
    jobs = RescanJobManager(service, cooldown_seconds=COOLDOWN_SECONDS, max_concurrent=MAX_CONCURRENT_RESCANS)
    RESCAN_API_ENABLED = (TRUSTED_PROXY or DIRECT_LOOPBACK_RESCAN) and bool(OPERATORS)
else:
    service = None


class CentralPoller:
    """Lifecycle-safe background poller for production inventory mode."""

    def __init__(self, poll_service: Any, *, interval_seconds: int | None = None) -> None:
        self.poll_service = poll_service
        raw_interval = poll_service.poll_interval_seconds if interval_seconds is None else interval_seconds
        self.interval_seconds = max(float(raw_interval), 0.001)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def thread_ident(self) -> int | None:
        thread = self._thread
        return thread.ident if thread is not None else None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="storage-viz-central-poller", daemon=True)
            self._thread.start()
            return True

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_service.poll_once()
            except Exception as exc:
                print(f"storage-viz central poll failed: {exc}", file=sys.stderr, flush=True)
            if self._stop.wait(self.interval_seconds):
                break


def build_central_poller(active_service: Any) -> CentralPoller | None:
    if isinstance(active_service, PollService):
        return CentralPoller(active_service)
    return None


central_poller = build_central_poller(service)


def _sign(text: str) -> str:
    return hmac.new(CSRF_SECRET.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _make_session_cookie(actor: str, now: int | None = None) -> str:
    ts = int(time.time()) if now is None else now
    payload = {"actor": actor, "iat": ts, "exp": ts + SESSION_TTL_SECONDS, "nonce": secrets.token_hex(16)}
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return encoded + "." + _sign(encoded)


def _parse_cookies(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and name not in out:
            out[name] = value
    return out


def _validate_session_cookie(value: str, actor: str, now: int | None = None) -> dict[str, Any] | None:
    if not isinstance(value, str) or len(value) > 1024 or "." not in value:
        return None
    encoded, sig = value.rsplit(".", 1)
    if not encoded or not hmac.compare_digest(_sign(encoded), sig):
        return None
    try:
        payload = json.loads(_unb64(encoded).decode("utf-8"))
    except Exception:
        return None
    ts = int(time.time()) if now is None else now
    if not isinstance(payload, dict) or set(payload) != {"actor", "iat", "exp", "nonce"}:
        return None
    if payload.get("actor") != actor:
        return None
    exp = payload.get("exp")
    iat = payload.get("iat")
    nonce = payload.get("nonce")
    if not isinstance(exp, int) or isinstance(exp, bool) or not isinstance(iat, int) or isinstance(iat, bool):
        return None
    if exp <= ts or iat > ts or exp - iat > max(SESSION_TTL_SECONDS, 1):
        return None
    if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        return None
    return payload


def _csrf_token(session_cookie: str) -> str:
    return hmac.new(CSRF_SECRET.encode("utf-8"), ("csrf:" + session_cookie).encode("utf-8"), hashlib.sha256).hexdigest()


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

    def _direct_loopback_origin(self) -> str | None:
        if not DIRECT_LOOPBACK_RESCAN:
            return None
        # Requests that traversed any proxy are not direct loopback clients,
        # even when a caller spoofs a loopback Host header.
        if self.headers.get("X-Forwarded-For", "").strip():
            return None
        host = self.headers.get("Host", "").strip()
        if not host or any(c in host for c in "\r\n/\\"):
            return None
        origin = "http://" + host
        if origin not in ALLOWED_ORIGINS or not _is_loopback_origin(origin):
            return None
        return origin

    def _session(self) -> tuple[int, dict[str, Any], str | None]:
        actor = self._actor()
        if actor is None:
            return 401, {"authenticated": False, "can_rescan": False}, None
        cookies = _parse_cookies(self.headers.get("Cookie", ""))
        cookie_value = cookies.get("storage_viz_session")
        if cookie_value and _validate_session_cookie(cookie_value, actor) is not None:
            new_cookie = None
        else:
            cookie_value = _make_session_cookie(actor)
            attrs = "Path=/; SameSite=Strict; HttpOnly; Secure" if TRUSTED_PROXY else "Path=/; SameSite=Lax; HttpOnly"
            new_cookie = f"storage_viz_session={cookie_value}; {attrs}"
        direct_origin = self._direct_loopback_origin()
        can_rescan = RESCAN_API_ENABLED and actor in OPERATORS and bool(ALLOWED_ORIGINS) and (TRUSTED_PROXY or direct_origin is not None)
        token = _csrf_token(cookie_value)
        return 200, {"authenticated": TRUSTED_PROXY, "actor": actor, "can_rescan": can_rescan, "csrf_token": token}, new_cookie

    def _require_read_auth(self) -> tuple[int, dict[str, Any]] | None:
        if not TRUSTED_PROXY:
            return None
        actor = self._actor()
        if actor is None:
            return 401, {"authenticated": False, "error": "AUTH_REQUIRED"}
        return None

    def _require_post_auth(self) -> tuple[int, dict[str, Any], str | None]:
        actor = self._actor()
        if actor is None:
            return 401, {"authenticated": False, "error": "AUTH_REQUIRED"}, None
        if not (RESCAN_API_ENABLED and actor in OPERATORS and ALLOWED_ORIGINS):
            return 403, {"error": "FORBIDDEN"}, None
        origin = self.headers.get("Origin", "")
        if not origin or origin not in ALLOWED_ORIGINS:
            return 403, {"error": "BAD_ORIGIN"}, None
        if DIRECT_LOOPBACK_RESCAN and origin != self._direct_loopback_origin():
            return 403, {"error": "BAD_ORIGIN"}, None
        cookies = _parse_cookies(self.headers.get("Cookie", ""))
        cookie_value = cookies.get("storage_viz_session", "")
        if _validate_session_cookie(cookie_value, actor) is None:
            return 403, {"error": "BAD_SESSION"}, None
        expected = _csrf_token(cookie_value)
        supplied = self.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, expected):
            return 403, {"error": "BAD_CSRF"}, None
        return 200, {"actor": actor}, actor

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
            auth_error = self._require_read_auth()
            if auth_error is not None:
                return self._json(auth_error[1], auth_error[0])
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
        auth_error = self._require_read_auth()
        if auth_error is not None:
            return self._json(auth_error[1], auth_error[0])
        if service is None:
            return self._json({"error": "api not configured"}, 503)
        if parts == ["api", "servers"]:
            return self._json({"data_mode": getattr(service, "data_mode", "inventory"), "servers": service.server_summaries()})
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
        if length == 0:
            return self._json({"error": "BAD_BODY"}, 400)
        raw_body = self.rfile.read(length)
        try:
            text = raw_body.decode("utf-8")
            decoder = json.JSONDecoder()
            body_obj, idx = decoder.raw_decode(text)
            if text[idx:].strip():
                return self._json({"error": "BAD_JSON"}, 400)
        except Exception:
            return self._json({"error": "BAD_JSON"}, 400)
        if not isinstance(body_obj, dict) or body_obj:
            return self._json({"error": "BAD_BODY"}, 400)
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
    PORT = _configured_port(include_argv=True)
    with Server((BIND, PORT), Handler) as httpd:
        host, port = httpd.server_address[:2]
        if central_poller is not None:
            central_poller.start()
        print(f"storage-viz serving {VIEWER_DIR} on {host}:{port}", flush=True)
        try:
            httpd.serve_forever()
        finally:
            if central_poller is not None:
                central_poller.stop()
