"""Versioned dashboard routes and the release asset allowlist."""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import posixpath
import re
import logging

from api.errors import RequestError
from collector.fleet import Fleet
from collector.ids import SERVER_ID_RE, VOLUME_ID_RE, SHA256_RE
from collector.view import activity_body, server_bytes, encode
from collector.errors import RescanDisabled, StoreUnavailable

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


def session(handler):
    body, cookie = handler._session()
    return json_response(body, headers={"Set-Cookie": cookie} if cookie else {})


def health_inventory(handler):
    # SHIM(M8..M9) S9: the installed health checker validates inventory order.
    runtime = handler.server.runtime
    return json_response({"data_mode": "inventory" if isinstance(runtime.fleet, Fleet) else "sample",
                          "servers": [{"id": s.id} for s in runtime.fleet.servers]})


def rescan(handler, server_id):
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        raise RequestError("BAD_REQUEST") from None
    if length < 0:
        raise RequestError("BAD_REQUEST")
    if length > 1024:
        raise RequestError("BODY_TOO_LARGE")
    if not length:
        raise RequestError("BAD_REQUEST")
    try:
        text = handler.rfile.read(length).decode().lstrip()
        body, end = json.JSONDecoder().raw_decode(text)
        if text[end:].strip():
            raise ValueError
    except (ValueError, RecursionError):
        raise RequestError("BAD_REQUEST") from None
    if body != {}:
        raise RequestError("BAD_REQUEST")
    actor = handler._require_post_auth()
    runtime = handler.server.runtime
    if runtime.fleet is None:
        raise RescanDisabled()
    if handler.health_probe:
        # SHIM(M8..M9) S9: the old checker submits a random absent hc- id.
        runtime.fleet.lookup(server_id)
        raise RequestError("NOT_FOUND")
    if not runtime.runs_actors:
        runtime.fleet.lookup(server_id)
        raise RescanDisabled()
    result = runtime.fleet.request_rescan(server_id, actor)
    return json_response({"job": result["job"]}, 202)


def fleet(handler):
    runtime = handler.server.runtime
    source = runtime.fleet
    now = source.clock.monotonic()
    rows = []
    for view in source.views():
        try:
            rows.append(server_bytes(view, now, runtime.capacity_thresholds))
        except Exception:
            logging.exception("failed to render server %s", view.server.id)
            rows.append(encode({"server_id": view.server.id, "status": "error", "error": {"code": "INTERNAL"}}))
    header = encode({"generated_unix": int(source.clock.time()), "rollout": source.rollout_view(),
                     "policy": {"capacity_thresholds": asdict(runtime.capacity_thresholds)}})
    return Response([header[:-1], b',"servers":[', b",".join(rows), b"]}"])


def checked_view(source, sid):
    view = source.view(sid)
    if view.error and view.publication is None:
        if view.error == StoreUnavailable.code:
            raise StoreUnavailable()
        raise RequestError(view.error)
    return view


def detail(handler, sid):
    runtime = handler.server.runtime
    view = checked_view(runtime.fleet, sid)
    return Response([server_bytes(view, runtime.fleet.clock.monotonic(), runtime.capacity_thresholds, detail=True)])


def activity(handler, sid):
    source = handler.server.runtime.fleet
    return json_response(activity_body(checked_view(source, sid), source.clock.monotonic()))


def capture(handler, sid, vid, sha):
    source = handler.server.runtime.fleet
    view = checked_view(source, sid)
    pub = view.publication
    manifest = pub.manifest if pub else None
    if manifest is None or manifest.volume(vid) is None:
        raise RequestError("UNKNOWN_VOLUME")
    if pub.captures.get(vid, {}).get("sha256") != sha:
        raise RequestError("CAPTURE_GONE")
    try:
        data = pub.legacy_bytes or source.read_capture(sid, vid, sha)
    except StoreUnavailable:
        if source.view(sid).data.accepted.get(vid) != sha:
            raise RequestError("CAPTURE_GONE") from None
        raise
    return Response([data], headers={"Content-Encoding": "gzip", "ETag": f'"{sha}"',
                                     "Cache-Control": "private, max-age=31536000, immutable"})


ROUTES = (
    ("GET", r"/api/v2/session", session, "session"),
    ("GET", r"/api/v2/fleet", fleet, "read"),
    ("GET", r"/api/v2/servers/([^/]+)", detail, "read"),
    ("GET", r"/api/v2/servers/([^/]+)/activity", activity, "read"),
    ("GET", r"/api/v2/servers/([^/]+)/volumes/([^/]+)/captures/([^/]+)", capture, "read"),
    ("POST", r"/api/v2/servers/([^/]+)/rescan", rescan, "rescan"),
    # SHIM(M8..M9) S9: only the three installed health-check requests.
    ("GET", r"/api/session", session, "session"),
    ("GET", r"/api/servers", health_inventory, "read"),
    ("POST", r"/api/servers/(hc-[A-Za-z0-9_]{24})/rescan", rescan, "rescan"),
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
        handler.health_probe = not path.startswith("/api/v2/")
        if not handler.health_probe:
            if "?" in target or any(not regex.fullmatch(param) for regex, param in
                                    zip((SERVER_ID_RE, VOLUME_ID_RE, SHA256_RE), match.groups())):
                raise RequestError("BAD_REQUEST")
        if auth == "read":
            handler._require_read_auth()
            if handler.server.runtime.fleet is None:
                raise StoreUnavailable()
        return action(handler, *match.groups())
    return static(handler, path)
