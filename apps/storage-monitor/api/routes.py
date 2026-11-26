"""Exact dashboard URLs and v1 projections (S5, until M8)."""
from dataclasses import dataclass, field
import json
from pathlib import Path
import posixpath
import re
import logging

from api.errors import RequestError
from collector.contract import snapshot_response
from collector.errors import RescanDisabled, SnapshotAbsent, StoreUnavailable
from collector.view import error_row_v1, job_v1, scan_status_v1, snapshot_v1_chunks, summary_v1

VIEWER_DIR = Path(__file__).resolve().parents[1] / "viewer"


# The release validator runs independently of the installed app. test_static
# compares both inventories so a release cannot omit or expose a viewer asset.
ASSETS = (
    "index.html",
    "api.js",
    "columns.js",
    "echarts.min.js",
    "effects.js",
    "format.js",
    "main.js",
    "removal.js",
    "router.js",
    "state.js",
    "store.js",
    "styles.css",
    "users-chart.js",
    "views/files.js",
    "views/header.js",
    "views/notices.js",
    "views/overview.js",
    "views/scan-status.js",
    "views/selection-bar.js",
    "views/server.js",
    "views/theme.js",
    "views/treemap.js",
    "views/users.js",
)
STATIC = {"/" + name: VIEWER_DIR / name for name in ASSETS}
STATIC["/"] = STATIC["/index.html"]
CONTENT_TYPES = {".js": "text/javascript", ".html": "text/html", ".css": "text/css"}


def static(handler, path):
    asset = STATIC.get(path)
    if asset is None:
        raise RequestError("NOT_FOUND")
    if handler.command not in ("GET", "HEAD"):
        raise RequestError("METHOD_NOT_ALLOWED", headers={"Allow": "GET, HEAD"})
    return Response([asset.read_bytes()], content_type=CONTENT_TYPES[asset.suffix])


@dataclass
class Response:
    chunks: list[bytes]
    status: int = 200
    headers: dict = field(default_factory=dict)
    content_type: str = "application/json"


def json_response(obj, status=200, headers=None):
    return Response([json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()], status, headers or {})


def overview(runtime, server_id, value):
    if runtime.live_capacity is not None:
        value = runtime.live_capacity.project(server_id, value)
    return snapshot_response(value, runtime.capacity_thresholds)


def summary(runtime, view, now):
    try:
        row = summary_v1(view, now)
        if row["overview_snapshot"] is not None:
            row["overview_snapshot"] = overview(runtime, view.server.id, row["overview_snapshot"])
        return row
    except Exception:
        logging.exception("storage-viz fleet row failed for %s", view.server.id)
        return error_row_v1(view.server, "INTERNAL")


def session(handler):
    body, cookie = handler._session()
    return json_response(body, headers={"Set-Cookie": cookie} if cookie else {})


def servers(handler):
    runtime = handler.server.runtime
    now = runtime.fleet.clock.monotonic()
    return json_response({"data_mode": runtime.data_mode, "servers": [summary(runtime, v, now) for v in runtime.fleet.views()]})


def scan_status(handler, server_id):
    runtime = handler.server.runtime
    view = runtime.fleet.view(server_id)
    status = scan_status_v1(view, runtime.fleet.clock.monotonic())
    if runtime.live_capacity is not None and view.snapshot is not None:
        value = view.snapshot.overview
        status["capacity"] = {"scan_generation": value["scan_generation"], "mounts": overview(runtime, server_id, value)["mounts"]}  # SHIM(M2b..M8) S5
    return json_response(status)


def snapshot(handler, server_id):
    runtime = handler.server.runtime
    view = runtime.fleet.view(server_id)
    if view.snapshot is None:
        raise StoreUnavailable() if view.error == StoreUnavailable.code else SnapshotAbsent()
    sample = runtime.live_capacity.sample_for(server_id) if runtime.live_capacity is not None else None
    return Response(snapshot_v1_chunks(view.snapshot, sample, runtime.capacity_thresholds))


def job(handler, server_id):
    view = handler.server.runtime.fleet.view(server_id)
    return json_response({"job": job_v1(view.job, server_id)})


def rescan(handler, server_id):
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        raise RequestError("BAD_LENGTH") from None
    if length < 0:
        raise RequestError("BAD_LENGTH")
    if length > 1024:
        raise RequestError("BODY_TOO_LARGE")
    if not length:
        raise RequestError("BAD_BODY")
    try:
        text = handler.rfile.read(length).decode()
        body, end = json.JSONDecoder().raw_decode(text)
        if text[end:].strip():
            raise ValueError
    except (ValueError, RecursionError):
        raise RequestError("BAD_JSON") from None
    if body != {}:
        raise RequestError("BAD_BODY")
    actor = handler._require_post_auth()
    runtime = handler.server.runtime
    if runtime.fleet is None:
        raise RescanDisabled()
    if not runtime.runs_actors:
        runtime.fleet.lookup(server_id)
        raise RescanDisabled(code="EXECUTION_DISABLED")
    return json_response(runtime.fleet.request_rescan(server_id, actor), 202)


def capabilities(handler):
    return json_response({"rescan": False, "message": "Manual rescan only: use the bounded per-server API behind a trusted proxy."})


def rescan_status(handler):
    return json_response({"supported": False, "scanning": False, "message": "Legacy local rescan endpoint is disabled."})


def legacy_rescan(handler):
    raise RequestError("LEGACY_DISABLED", body={"supported": False, "error": "Legacy local rescan endpoint is disabled."})


ROUTES = (
    ("GET", r"/api/session", session, "session"),
    ("GET", r"/api/servers", servers, "read"),
    ("GET", r"/api/servers/([^/]+)/scan-status", scan_status, "read"),
    ("GET", r"/api/servers/([^/]+)/snapshot", snapshot, "read"),
    ("GET", r"/api/servers/([^/]+)/job", job, "read"),
    ("POST", r"/api/servers/([^/]+)/rescan", rescan, "rescan"),
    ("GET", r"/capabilities", capabilities, "public"),
    ("GET", r"/rescan-status", rescan_status, "public"),
    ("POST", r"/rescan", legacy_rescan, "public"),
)


def dispatch(handler, target):
    path = target.partition("?")[0]
    if (not path.startswith("/") or path.startswith("//") or posixpath.normpath(path) != path
            or any(c in path for c in "%\\\x00#")):
        raise RequestError("NOT_FOUND")
    for method, pattern, action, auth in ROUTES:
        match = re.fullmatch(pattern, path)
        if match is None:
            continue
        if handler.command != method:
            raise RequestError("METHOD_NOT_ALLOWED", headers={"Allow": method})
        handler.v1_route = True
        if auth == "read":
            handler._require_read_auth()
            if handler.server.runtime.fleet is None:
                raise RequestError("UNCONFIGURED", body={"error": "api not configured"})
        return action(handler, *match.groups())
    return static(handler, path)
