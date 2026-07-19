"""Strict central inventory validation for multi-server storage-viz."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
import pathlib
import re
from typing import Any, Dict, Mapping, Tuple

SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$|^localhost$")
MAX_INT = 10**18

SCANNER_ALLOWED_KEYS = frozenset({
    "server_id",
    "scanner_path",
    "data_dir",
    "run_dir",
    "threads",
    "prune_home_mb",
    "prune_data_mb",
    "top",
    "stale_days",
})
SCANNER_FORBIDDEN_KEYS = frozenset({
    "targets",
    "include_mounts",
    "exclude_mounts",
    "scan_roots",
    "/",
    "mounts",
    "mountpoints",
    "root",
    "roots",
    "paths",
    "path",
    "include_paths",
    "exclude_paths",
    "include",
    "exclude",
})
SERVER_ALLOWED_KEYS = frozenset({
    "id",
    "display_name",
    "order",
    "host",
    "port",
    "enabled",
    "username",
    "identity_file",
    "known_hosts_file",
    "scanner",
})
SECURITY_FORBIDDEN_KEYS = frozenset({
    "password", "passphrase", "token", "secret", "private_key", "private_key_data",
    "key", "key_data", "admin_user", "admin_password", "sudo_password",
    "ProxyCommand", "proxy_command", "command", "shell", "args", "argv", "ssh_args",
}) | SCANNER_FORBIDDEN_KEYS
INLINE_SECRET_MARKERS = ("-----BEGIN ", "PRIVATE KEY", "ssh-ed25519 ", "ssh-rsa ", "token=", "password=")


@dataclass(frozen=True)
class CapacityThresholds:
    warning_used_pct: int = 80
    critical_used_pct: int = 92
    warning_free_bytes: int = 549755813888
    critical_free_bytes: int = 137438953472

    @classmethod
    def default(cls) -> "CapacityThresholds":
        return cls()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "CapacityThresholds":
        if raw is None:
            return cls.default()
        if not isinstance(raw, Mapping):
            raise ValueError("capacity_thresholds must be an object")
        allowed = {"warning_used_pct", "critical_used_pct", "warning_free_bytes", "critical_free_bytes"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown capacity_thresholds key(s): {', '.join(sorted(unknown))}")
        values = {
            "warning_used_pct": _bounded_int(raw.get("warning_used_pct", 80), "warning_used_pct", 0, 100),
            "critical_used_pct": _bounded_int(raw.get("critical_used_pct", 92), "critical_used_pct", 0, 100),
            "warning_free_bytes": _bounded_int(raw.get("warning_free_bytes", 549755813888), "warning_free_bytes", 0, MAX_INT - 1),
            "critical_free_bytes": _bounded_int(raw.get("critical_free_bytes", 137438953472), "critical_free_bytes", 0, MAX_INT - 1),
        }
        if values["warning_used_pct"] >= values["critical_used_pct"]:
            raise ValueError("warning_used_pct must be less than critical_used_pct")
        if values["warning_free_bytes"] <= values["critical_free_bytes"]:
            raise ValueError("warning_free_bytes must be greater than critical_free_bytes")
        return cls(**values)

    def pressure(self, *, used_pct: int, free_bytes: int) -> str:
        used = _bounded_int(used_pct, "used_pct", 0, 100)
        free = _bounded_int(free_bytes, "free_bytes", 0, MAX_INT - 1)
        if used >= self.critical_used_pct or free <= self.critical_free_bytes:
            return "critical"
        if used >= self.warning_used_pct or free <= self.warning_free_bytes:
            return "warning"
        return "normal"


@dataclass(frozen=True)
class Server:
    id: str
    display_name: str
    order: int
    host: str
    port: int
    enabled: bool
    username: str
    identity_file: pathlib.PurePosixPath
    known_hosts_file: pathlib.PurePosixPath
    scanner: Mapping[str, Any]
    scanner_digest: str


@dataclass(frozen=True)
class Inventory:
    servers: Tuple[Server, ...]
    capacity_thresholds: CapacityThresholds


def load_inventory(path: str | pathlib.Path) -> Inventory:
    cfg_path = pathlib.Path(path)
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"inventory must be strict JSON-compatible YAML parsed by json: {exc}") from exc
    return parse_inventory(raw)


def parse_inventory(raw: Any) -> Inventory:
    if not isinstance(raw, Mapping):
        raise ValueError("inventory top level must be an object")
    allowed = {"servers", "capacity_thresholds"}
    unknown = set(raw) - allowed - SECURITY_FORBIDDEN_KEYS
    forbidden = set(raw) & SECURITY_FORBIDDEN_KEYS
    if forbidden:
        raise ValueError(f"forbidden inventory key(s): {', '.join(sorted(forbidden))}")
    if unknown:
        raise ValueError(f"unknown inventory key(s): {', '.join(sorted(unknown))}")
    servers_raw = raw.get("servers")
    if not isinstance(servers_raw, list) or not servers_raw:
        raise ValueError("servers must be a non-empty array")
    thresholds = CapacityThresholds.from_mapping(raw.get("capacity_thresholds"))
    servers = []
    seen = set()
    orders = set()
    for idx, item in enumerate(servers_raw):
        server = _parse_server(item, idx)
        if server.id in seen:
            raise ValueError(f"duplicate server id: {server.id}")
        if server.order in orders:
            raise ValueError(f"duplicate order: {server.order}")
        seen.add(server.id)
        orders.add(server.order)
        servers.append(server)
    return Inventory(tuple(servers), thresholds)


def _parse_server(raw: Any, idx: int) -> Server:
    if not isinstance(raw, Mapping):
        raise ValueError(f"servers[{idx}] must be an object")
    forbidden = set(raw) & SECURITY_FORBIDDEN_KEYS
    unknown = set(raw) - SERVER_ALLOWED_KEYS - SECURITY_FORBIDDEN_KEYS
    if forbidden:
        raise ValueError(f"forbidden server key(s): {', '.join(sorted(forbidden))}")
    if unknown:
        raise ValueError(f"unknown server key(s): {', '.join(sorted(unknown))}")
    sid = _safe_id(_string(raw.get("id"), "server id"), "server id")
    display_name = _string(raw.get("display_name"), "display_name")
    order = _bounded_int(raw.get("order", idx), "order", 0, 1_000_000)
    host = _validate_host(_string(raw.get("host"), "host"))
    port = _bounded_int(raw.get("port", 22), "port", 1, 65535)
    enabled = _bool(raw.get("enabled", True), "enabled")
    username = _string(raw.get("username"), "username")
    if username != "monitoring":
        raise ValueError("username must be exactly monitoring")
    identity_file = _safe_absolute_external_path(_string(raw.get("identity_file"), "identity_file"), "identity_file")
    known_hosts_file = _safe_absolute_external_path(_string(raw.get("known_hosts_file"), "known_hosts_file"), "known_hosts_file")
    scanner = _parse_scanner(raw.get("scanner", {}), sid)
    digest = scanner_digest(scanner)
    return Server(sid, display_name, order, host, port, enabled, username, identity_file, known_hosts_file, scanner, digest)


def scanner_digest(scanner: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(scanner, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _parse_scanner(raw: Any, sid: str) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("scanner must be an object")
    forbidden = set(raw) & SCANNER_FORBIDDEN_KEYS
    unknown = set(raw) - SCANNER_ALLOWED_KEYS - SCANNER_FORBIDDEN_KEYS
    if forbidden:
        raise ValueError(f"forbidden scanner key(s): {', '.join(sorted(forbidden))}")
    if unknown:
        raise ValueError(f"unknown scanner key(s): {', '.join(sorted(unknown))}")
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if key == "server_id":
            value = _safe_id(_string(value, "scanner.server_id"), "scanner.server_id")
            if value != sid:
                raise ValueError("scanner.server_id must match server id")
        elif key in {"scanner_path", "data_dir", "run_dir"}:
            value = str(_safe_absolute_path(_string(value, f"scanner.{key}"), f"scanner.{key}"))
        elif key == "threads":
            value = _bounded_int(value, "scanner.threads", 1, 64)
        elif key in {"prune_home_mb", "prune_data_mb"}:
            value = _bounded_int(value, f"scanner.{key}", 0, 10_000_000)
        elif key == "top":
            value = _bounded_int(value, "scanner.top", 0, 100_000)
        elif key == "stale_days":
            value = _bounded_int(value, "scanner.stale_days", 0, 100_000)
        out[key] = value
    return out


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if any(marker in value for marker in INLINE_SECRET_MARKERS):
        raise ValueError(f"{name} appears to contain inline secret data")
    return value


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _bounded_int(value: Any, name: str, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < lo or value > hi:
        raise ValueError(f"{name} must be in [{lo}, {hi}]")
    return value


def _safe_id(value: str, name: str) -> str:
    if not SERVER_ID_RE.match(value) or value in {".", ".."}:
        raise ValueError(f"{name} must match ^[A-Za-z0-9_.-]+$")
    return value


def _validate_host(value: str) -> str:
    if any(ch in value for ch in " ;|&`$\\\n\r\t/[]{}()<>"):
        raise ValueError("host contains unsafe characters")
    try:
        ip = ipaddress.ip_address(value)
        if ip.version != 4:
            raise ValueError("host must be safe DNS or IPv4")
        return value
    except ValueError:
        pass
    if not HOST_RE.match(value):
        raise ValueError("host must be safe DNS or IPv4")
    return value


def _safe_absolute_path(value: str, name: str) -> pathlib.PurePosixPath:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{name} contains control characters")
    path = pathlib.PurePosixPath(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    if value != "/" and value.endswith("/"):
        raise ValueError(f"{name} must not have trailing slash")
    if os.path.normpath(value) != value:
        raise ValueError(f"{name} must be canonical POSIX path")
    return path

def _safe_absolute_external_path(value: str, name: str) -> pathlib.PurePosixPath:
    path = _safe_absolute_path(value, name)
    parts = set(path.parts)
    if path.parts[:3] != ("/", "etc", "storage-viz"):
        raise ValueError(f"{name} must be under /etc/storage-viz")
    return path
