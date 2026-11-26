"""Trusted proxy, session and CSRF checks for v1 requests."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

from api.errors import RequestError

BIND = os.environ.get("STORAGE_VIZ_BIND", os.environ.get("BIND", "127.0.0.1"))
TRUSTED_PROXY = os.environ.get("STORAGE_VIZ_TRUSTED_PROXY", "").lower() in {"1", "true", "yes", "on"}
DIRECT_LOOPBACK_RESCAN = os.environ.get("STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN", "").lower() in {"1", "true", "yes", "on"}
PREFLIGHT_BACKEND = os.environ.get("STORAGE_VIZ_PREFLIGHT_BACKEND", "").lower() in {"1", "true", "yes", "on"}
IDENTITY_HEADER = os.environ.get("STORAGE_VIZ_IDENTITY_HEADER", "X-Forwarded-User")
ALLOWED_ORIGINS = frozenset(v.strip() for v in os.environ.get("STORAGE_VIZ_ALLOWED_ORIGINS", "").split(",") if v.strip())
OPERATORS = frozenset(v.strip() for v in os.environ.get("STORAGE_VIZ_OPERATOR_ALLOWLIST", "").split(",") if v.strip())
SESSION_COOKIE_SECURE_RAW = os.environ.get("STORAGE_VIZ_SESSION_COOKIE_SECURE")
CSRF_SECRET = os.environ.get("STORAGE_VIZ_CSRF_SECRET", secrets.token_hex(32))
SESSION_TTL_SECONDS = int(os.environ.get("STORAGE_VIZ_SESSION_TTL_SECONDS", "3600"))


def _parse_bool_setting(name: str, raw: str | None, *, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean")


SESSION_COOKIE_SECURE = _parse_bool_setting(
    "STORAGE_VIZ_SESSION_COOKIE_SECURE",
    SESSION_COOKIE_SECURE_RAW,
    default=TRUSTED_PROXY,
)


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


def _is_exact_http_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.netloc.endswith(":")
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
    )


INVENTORY_PATH = os.environ.get("STORAGE_VIZ_INVENTORY", "").strip()

if TRUSTED_PROXY and not _is_loopback(BIND):
    raise SystemExit("trusted-proxy mode requires a loopback bind")
if TRUSTED_PROXY and OPERATORS and not ALLOWED_ORIGINS:
    raise SystemExit("trusted-proxy operator mode requires at least one exact allowed origin")
if SESSION_COOKIE_SECURE_RAW is not None and not TRUSTED_PROXY:
    raise SystemExit("STORAGE_VIZ_SESSION_COOKIE_SECURE is only valid in trusted-proxy mode")
if TRUSTED_PROXY and not SESSION_COOKIE_SECURE:
    if not ALLOWED_ORIGINS or not all(_is_exact_http_origin(origin) for origin in ALLOWED_ORIGINS):
        raise SystemExit("non-secure trusted-proxy sessions require exact HTTP origins")
if PREFLIGHT_BACKEND:
    if not TRUSTED_PROXY:
        raise SystemExit("preflight backend mode requires trusted-proxy mode")
    if not _is_loopback(BIND):
        raise SystemExit("preflight backend mode requires a loopback bind")
    if DIRECT_LOOPBACK_RESCAN:
        raise SystemExit("preflight backend mode rejects direct rescan mode")
    if not INVENTORY_PATH:
        raise SystemExit("preflight backend mode requires production inventory")
if DIRECT_LOOPBACK_RESCAN:
    if TRUSTED_PROXY:
        raise SystemExit("direct loopback rescan mode cannot be combined with trusted-proxy mode")
    if not _is_loopback(BIND):
        raise SystemExit("direct loopback rescan mode requires a loopback bind")
    if not INVENTORY_PATH:
        raise SystemExit("direct loopback rescan mode requires production inventory")
    if "direct-viewer" not in OPERATORS:
        raise SystemExit("direct loopback rescan mode requires direct-viewer in operator allowlist")
    if not ALLOWED_ORIGINS:
        raise SystemExit("direct loopback rescan mode requires at least one exact allowed origin")
    if not all(_is_loopback_origin(origin) for origin in ALLOWED_ORIGINS):
        raise SystemExit("direct loopback rescan mode accepts loopback HTTP origins only")


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


class Auth:
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

    def _session(self) -> tuple[dict[str, Any], str | None]:
        actor = self._actor()
        if actor is None:
            raise RequestError("AUTH_REQUIRED", body={"authenticated": False, "can_rescan": False})
        cookies = _parse_cookies(self.headers.get("Cookie", ""))
        cookie_value = cookies.get("storage_viz_session")
        if cookie_value and _validate_session_cookie(cookie_value, actor) is not None:
            new_cookie = None
        else:
            cookie_value = _make_session_cookie(actor)
            same_site = "Strict" if TRUSTED_PROXY else "Lax"
            attrs = f"Path=/; SameSite={same_site}; HttpOnly"
            if SESSION_COOKIE_SECURE:
                attrs += "; Secure"
            new_cookie = f"storage_viz_session={cookie_value}; {attrs}"
        direct_origin = self._direct_loopback_origin()
        can_rescan = self.server.runtime.rescan_enabled and actor in OPERATORS and bool(ALLOWED_ORIGINS) and (TRUSTED_PROXY or direct_origin is not None)
        token = _csrf_token(cookie_value)
        return {"authenticated": TRUSTED_PROXY, "actor": actor, "can_rescan": can_rescan, "csrf_token": token}, new_cookie

    def _require_read_auth(self) -> None:
        if not TRUSTED_PROXY:
            return None
        actor = self._actor()
        if actor is None:
            raise RequestError("AUTH_REQUIRED", body={"authenticated": False, "error": "AUTH_REQUIRED"})
        return None

    def _require_post_auth(self) -> str:
        actor = self._actor()
        if actor is None:
            raise RequestError("AUTH_REQUIRED", body={"authenticated": False, "error": "AUTH_REQUIRED"})
        if not (self.server.runtime.rescan_enabled and actor in OPERATORS and ALLOWED_ORIGINS):
            raise RequestError("FORBIDDEN")
        origin = self.headers.get("Origin", "")
        if not origin or origin not in ALLOWED_ORIGINS:
            raise RequestError("BAD_ORIGIN")
        if DIRECT_LOOPBACK_RESCAN and origin != self._direct_loopback_origin():
            raise RequestError("BAD_ORIGIN")
        cookies = _parse_cookies(self.headers.get("Cookie", ""))
        cookie_value = cookies.get("storage_viz_session", "")
        if _validate_session_cookie(cookie_value, actor) is None:
            raise RequestError("BAD_SESSION")
        expected = _csrf_token(cookie_value)
        supplied = self.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise RequestError("BAD_CSRF")
        return actor
