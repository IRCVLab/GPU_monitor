#!/usr/bin/env python3.12
"""Production health check for the storage-viz dashboard behind the public proxy."""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import http.client
import ipaddress
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHELLY_VALUE_RE = re.compile(r"[\s`$'\";&|<>\\]")
FIXED_PROXY_OPERATOR = "fixed-proxy-operator"
DASHBOARD_SERVICE = "storage-viz-dashboard.service"
PROXY_SERVICE = "storage-viz-proxy.service"
PROXY_ENV_KEYS = frozenset(
    {
        "STORAGE_VIZ_PROXY_BIND",
        "STORAGE_VIZ_PROXY_PORT",
        "STORAGE_VIZ_PROXY_UPSTREAM_HOST",
        "STORAGE_VIZ_PROXY_UPSTREAM_PORT",
        "STORAGE_VIZ_PROXY_OPERATOR",
        "STORAGE_VIZ_PROXY_PUBLIC_ORIGIN",
        "STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES",
    }
)
MAX_PROXY_RESPONSE_BYTES = 512 * 1024 * 1024


class HealthCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class HealthContract:
    dashboard_env: Mapping[str, str]
    proxy_env: Mapping[str, str]
    inventory_path: Path
    enabled_server_ids: list[str]
    public_origin: str
    public_host: str
    upstream_host: str = "127.0.0.1"
    upstream_port: int = 8088
    public_port: int = 505
    connect_host: str | None = None
    connect_port: int | None = None


def parse_environment_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or "=" not in line:
            raise HealthCheckError(f"{path}:{lineno}: malformed EnvironmentFile line")
        key, value = line.split("=", 1)
        if not ENV_KEY_RE.fullmatch(key):
            raise HealthCheckError(f"{path}:{lineno}: malformed key")
        if key in out:
            raise HealthCheckError(f"{path}:{lineno}: duplicate key {key}")
        if value == "" or value != value.strip() or SHELLY_VALUE_RE.search(value):
            raise HealthCheckError(f"{path}:{lineno}: malformed value for {key}")
        out[key] = value
    return out


def _require(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise HealthCheckError(f"missing required key {key}")
    return value


def _require_bool(env: Mapping[str, str], key: str, expected: str) -> None:
    value = _require(env, key).lower()
    truth = {"1": "1", "true": "1", "yes": "1", "on": "1", "0": "0", "false": "0", "no": "0", "off": "0"}.get(value)
    if truth != expected:
        raise HealthCheckError(f"{key} must be {expected}")


def _parse_origin(origin: str) -> tuple[str, int, str]:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise HealthCheckError(f"invalid origin {origin}") from exc
    if parsed.scheme != "http" or not parsed.hostname or port is None or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise HealthCheckError(f"origin must be exact HTTP origin: {origin}")
    return parsed.hostname, port, parsed.netloc


def _validate_proxy_bind(value: str) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise HealthCheckError("STORAGE_VIZ_PROXY_BIND must be an IP address") from exc
    if address.version != 4 or address.is_loopback:
        raise HealthCheckError("STORAGE_VIZ_PROXY_BIND must be a non-loopback IPv4 address")


def _load_enabled_server_ids(path: Path) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HealthCheckError(f"inventory JSON invalid: {exc}") from exc
    servers = raw.get("servers") if isinstance(raw, Mapping) else None
    if not isinstance(servers, list) or not servers:
        raise HealthCheckError("inventory servers must be a non-empty array")
    ids: list[str] = []
    seen: set[str] = set()
    for row in servers:
        if not isinstance(row, Mapping):
            raise HealthCheckError("inventory server rows must be objects")
        sid = row.get("id")
        if not isinstance(sid, str) or not SERVER_ID_RE.fullmatch(sid):
            raise HealthCheckError("inventory server id invalid")
        if sid in seen:
            raise HealthCheckError("duplicate inventory server id")
        seen.add(sid)
        if row.get("enabled", True) is True:
            ids.append(sid)
    if not ids:
        raise HealthCheckError("inventory must contain enabled servers")
    return ids


def load_contract(
    *,
    dashboard_env: Path = Path("/etc/storage-viz/dashboard.env"),
    proxy_env: Path = Path("/etc/storage-viz/proxy.env"),
    connect_host: str | None = None,
    connect_port: int | None = None,
) -> HealthContract:
    dash = parse_environment_file(Path(dashboard_env))
    proxy = parse_environment_file(Path(proxy_env))
    for key in (
        "STORAGE_VIZ_BIND", "STORAGE_VIZ_PORT", "STORAGE_VIZ_TRUSTED_PROXY", "STORAGE_VIZ_ALLOWED_ORIGINS",
        "STORAGE_VIZ_OPERATOR_ALLOWLIST", "STORAGE_VIZ_SESSION_COOKIE_SECURE", "STORAGE_VIZ_INVENTORY",
    ):
        _require(dash, key)
    for key in (
        "STORAGE_VIZ_PROXY_BIND", "STORAGE_VIZ_PROXY_PORT", "STORAGE_VIZ_PROXY_UPSTREAM_HOST",
        "STORAGE_VIZ_PROXY_UPSTREAM_PORT", "STORAGE_VIZ_PROXY_OPERATOR", "STORAGE_VIZ_PROXY_PUBLIC_ORIGIN",
    ):
        _require(proxy, key)
    unknown_proxy_keys = set(proxy) - PROXY_ENV_KEYS
    if unknown_proxy_keys:
        raise HealthCheckError(f"unsupported proxy EnvironmentFile keys: {', '.join(sorted(unknown_proxy_keys))}")
    if dash.get("STORAGE_VIZ_DEV_SAMPLE_DIR") or dash.get("STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN"):
        raise HealthCheckError("production health rejects dev sample/direct modes")
    if dash["STORAGE_VIZ_BIND"] != "127.0.0.1" or dash["STORAGE_VIZ_PORT"] != "8088":
        raise HealthCheckError("dashboard must bind 127.0.0.1:8088")
    if proxy["STORAGE_VIZ_PROXY_PORT"] != "505":
        raise HealthCheckError("public proxy port must be 505")
    _validate_proxy_bind(proxy["STORAGE_VIZ_PROXY_BIND"])
    if proxy["STORAGE_VIZ_PROXY_UPSTREAM_HOST"] != "127.0.0.1" or proxy["STORAGE_VIZ_PROXY_UPSTREAM_PORT"] != "8088":
        raise HealthCheckError("proxy upstream must be 127.0.0.1:8088")
    if "STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES" in proxy:
        try:
            max_response_bytes = int(proxy["STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES"])
        except ValueError as exc:
            raise HealthCheckError("STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES must be an integer") from exc
        if not 1 <= max_response_bytes <= MAX_PROXY_RESPONSE_BYTES:
            raise HealthCheckError("STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES is outside the allowed bound")
    _require_bool(dash, "STORAGE_VIZ_TRUSTED_PROXY", "1")
    _require_bool(dash, "STORAGE_VIZ_SESSION_COOKIE_SECURE", "0")
    public_origin = proxy["STORAGE_VIZ_PROXY_PUBLIC_ORIGIN"]
    _, port, host = _parse_origin(public_origin)
    if port != 505:
        raise HealthCheckError("public origin must exactly describe real public :505")
    if dash["STORAGE_VIZ_ALLOWED_ORIGINS"] != public_origin:
        raise HealthCheckError("dashboard allowed origin must exactly match public origin")
    operator = proxy["STORAGE_VIZ_PROXY_OPERATOR"]
    if operator != FIXED_PROXY_OPERATOR or operator not in dash["STORAGE_VIZ_OPERATOR_ALLOWLIST"].split(","):
        raise HealthCheckError("fixed proxy operator must be in dashboard rescan allowlist")
    inventory_path = Path(dash["STORAGE_VIZ_INVENTORY"])
    if inventory_path.name != "servers.json":
        raise HealthCheckError("production inventory path must end in servers.json")
    if (connect_host is None) != (connect_port is None):
        raise HealthCheckError("candidate connection override requires both host and port")
    if connect_host is not None and (connect_host != "127.0.0.1" or connect_port != 1505):
        raise HealthCheckError("candidate connection override must be exactly 127.0.0.1:1505")
    return HealthContract(
        dash,
        proxy,
        inventory_path,
        _load_enabled_server_ids(inventory_path),
        public_origin,
        host,
        connect_host=connect_host,
        connect_port=connect_port,
    )


def _json_response(response: Any) -> tuple[int, Mapping[str, str], Any]:
    raw = response.read()
    try:
        body = json.loads(raw.decode("utf-8") if raw else "{}")
    except Exception as exc:
        raise HealthCheckError(f"invalid JSON response: {exc}") from exc
    return response.status, dict(response.getheaders()), body


def _unknown_id(absent_from: set[str]) -> str:
    for _ in range(64):
        candidate = "hc-" + secrets.token_urlsafe(18).replace("-", "_")[:32]
        if SERVER_ID_RE.fullmatch(candidate) and candidate not in absent_from:
            return candidate
    raise HealthCheckError("could not generate absent server id")


def _request(connection_factory: Callable[..., Any], contract: HealthContract, method: str, path: str, *, body: bytes | None = None, headers: Mapping[str, str] | None = None) -> tuple[int, Mapping[str, str], Any]:
    connection_host = contract.connect_host or contract.public_host.rsplit(":", 1)[0]
    connection_port = contract.connect_port or contract.public_port
    conn = connection_factory(connection_host, connection_port, timeout=5)
    try:
        conn.request(method, path, body=body, headers={"Host": contract.public_host, **dict(headers or {})})
        return _json_response(conn.getresponse())
    finally:
        conn.close()


def _service_active(name: str, runner: Callable[..., Any]) -> None:
    result = runner(["systemctl", "is-active", name], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if getattr(result, "returncode", 1) != 0 or str(getattr(result, "stdout", "")).strip() != "active":
        raise HealthCheckError(f"systemd service inactive: {name}")


def run_health_check(
    contract: HealthContract,
    *,
    skip_service_check: bool = False,
    runner: Callable[..., Any] = subprocess.run,
    connection_factory: Callable[..., Any] = http.client.HTTPConnection,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not skip_service_check:
        for service in (DASHBOARD_SERVICE, PROXY_SERVICE):
            _service_active(service, runner)
    last_error: Exception | None = None
    for _ in range(3):
        try:
            status, headers, session = _request(connection_factory, contract, "GET", "/api/session", headers={"X-Forwarded-User": FIXED_PROXY_OPERATOR})
            if status != 200 or not isinstance(session, Mapping) or session.get("can_rescan") is not True or not isinstance(session.get("csrf_token"), str):
                raise HealthCheckError("session probe failed or can_rescan false")
            cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
            if not cookie.startswith("storage_viz_session="):
                raise HealthCheckError("session cookie missing")
            status, _, servers = _request(connection_factory, contract, "GET", "/api/servers", headers={"Cookie": cookie, "X-Forwarded-User": FIXED_PROXY_OPERATOR})
            if status != 200 or not isinstance(servers, Mapping) or servers.get("data_mode") != "inventory":
                raise HealthCheckError("servers probe failed or sample mode")
            observed = [s.get("id") for s in servers.get("servers", []) if isinstance(s, Mapping)]
            if observed != contract.enabled_server_ids:
                raise HealthCheckError("enabled server IDs missing or reordered")
            absent = _unknown_id(set(contract.enabled_server_ids))
            post_headers = {"Cookie": cookie, "X-Forwarded-User": FIXED_PROXY_OPERATOR, "X-CSRF-Token": str(session["csrf_token"]), "Origin": contract.public_origin}
            status, _, body = _request(connection_factory, contract, "POST", f"/api/servers/{absent}/rescan", body=b"{}", headers=post_headers)
            if (
                status != 404
                or not isinstance(body, Mapping)
                or body.get("error") != "UNKNOWN_SERVER"
            ):
                raise HealthCheckError("unknown-server rescan probe failed")
            return
        except Exception as exc:
            last_error = exc
            sleep(0.2)
    raise HealthCheckError(str(last_error or "health check failed"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard-env", default="/etc/storage-viz/dashboard.env")
    parser.add_argument("--proxy-env", default="/etc/storage-viz/proxy.env")
    parser.add_argument("--connect-host")
    parser.add_argument("--connect-port", type=int)
    parser.add_argument("--skip-service-check", action="store_true")
    args = parser.parse_args(argv)
    contract = load_contract(
        dashboard_env=Path(args.dashboard_env),
        proxy_env=Path(args.proxy_env),
        connect_host=args.connect_host,
        connect_port=args.connect_port,
    )
    run_health_check(contract, skip_service_check=args.skip_service_check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
