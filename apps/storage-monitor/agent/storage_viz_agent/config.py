from dataclasses import dataclass, field
import hashlib
import json
import os
import pathlib
from typing import Any, Mapping, Union

from .ids import SERVER_ID_RE

DEFAULT_DATA_DIR = "/var/lib/storage-viz"
DEFAULT_RUN_DIR = "/run/storage-viz"
_ALLOWED_KEYS = frozenset({
    "server_id",
    "volume_aliases",
    "scanner_path",
    "data_dir",
    "run_dir",
    "threads",
    "prune_home_mb",
    "prune_data_mb",
    "top",
    "stale_days",
    "host_profile",
    "volume_media",
    "display_prefixes",
})
HOST_PROFILES = ("standard", "truenas")
MEDIA_OVERRIDES = ("ssd", "hdd")
_FORBIDDEN_KEYS = frozenset({
    "targets",
    "include_mounts",
    "exclude_mounts",
    "/",
    "mounts",
    "mountpoints",
    "scan_roots",
    "root",
    "roots",
    "paths",
    "path",
    "include_paths",
    "exclude_paths",
    "include",
    "exclude",
})


@dataclass(frozen=True)
class ScannerConfig:
    server_id: str
    scanner_path: str
    data_dir: pathlib.Path
    run_dir: pathlib.Path
    threads: int
    prune_home_mb: int
    prune_data_mb: int
    top: int
    stale_days: int
    volume_aliases: dict
    config_digest: str
    host_profile: str = "standard"
    """``truenas`` keeps only the data-pool datasets under /mnt (mount_policy.truenas_classifier)."""
    volume_media: Mapping[str, str] = field(default_factory=dict)
    """mountpoint -> ssd or hdd, replacing the sysfs media classification of that mount."""
    display_prefixes: Mapping[str, str] = field(default_factory=dict)
    """host path prefix -> the path users see (Nexus: /mnt/tank -> /nexus, as clients mount it).
    Only published paths change; scanning, identity and bindings use the real paths."""


def _canonical_json(data: Mapping[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_config(path: Union[os.PathLike, str]) -> ScannerConfig:
    cfg_path = pathlib.Path(path)
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"config must be strict JSON-compatible YAML parsed by json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    keys = set(raw)
    forbidden = keys & _FORBIDDEN_KEYS
    if forbidden:
        raise ValueError(f"forbidden config key(s): {', '.join(sorted(forbidden))}")
    unknown = keys - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"unknown config key(s): {', '.join(sorted(unknown))}")
    for required in ("server_id", "scanner_path"):
        if required not in raw:
            raise ValueError(f"missing required config key: {required}")

    server_id = _string(raw["server_id"], "server_id")
    if not SERVER_ID_RE.fullmatch(server_id):
        raise ValueError("server_id is invalid")
    scanner_path = _absolute_path(_string(raw["scanner_path"], "scanner_path"), "scanner_path")
    data_dir = pathlib.Path(_absolute_path(_string(raw.get("data_dir", DEFAULT_DATA_DIR), "data_dir"), "data_dir"))
    run_dir = pathlib.Path(_absolute_path(_string(raw.get("run_dir", DEFAULT_RUN_DIR), "run_dir"), "run_dir"))
    threads = _int_range(raw.get("threads", 2), "threads", 1, 64)
    prune_home_mb = _int_range(raw.get("prune_home_mb", 50), "prune_home_mb", 0, 10_000_000)
    prune_data_mb = _int_range(raw.get("prune_data_mb", 100), "prune_data_mb", 0, 10_000_000)
    top = _int_range(raw.get("top", 200), "top", 0, 100_000)
    stale_days = _int_range(raw.get("stale_days", 180), "stale_days", 0, 100_000)
    aliases = raw.get("volume_aliases", {})
    if not isinstance(aliases, dict) or any(not k.startswith("/") or not isinstance(v, str) or not v or "\0" in v for k, v in aliases.items()):
        raise ValueError("volume_aliases must map absolute paths to nonempty names")
    host_profile = raw.get("host_profile", "standard")
    if host_profile not in HOST_PROFILES:
        raise ValueError(f"host_profile must be one of {', '.join(HOST_PROFILES)}")
    media = raw.get("volume_media", {})
    if not isinstance(media, dict) or any(not k.startswith("/") or v not in MEDIA_OVERRIDES for k, v in media.items()):
        raise ValueError(f"volume_media must map absolute mountpoints to one of {', '.join(MEDIA_OVERRIDES)}")
    prefixes = raw.get("display_prefixes", {})
    if not isinstance(prefixes, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or not k.startswith("/") or not v.startswith("/")
            or k == "/" or k != k.rstrip("/") or v != v.rstrip("/") or "\0" in k + v for k, v in prefixes.items()):
        raise ValueError("display_prefixes must map absolute path prefixes (not /) to absolute path prefixes")
    digest = hashlib.sha256(_canonical_json(raw)).hexdigest()
    return ScannerConfig(server_id, scanner_path, data_dir, run_dir, threads, prune_home_mb, prune_data_mb, top, stale_days,
                         aliases, digest, host_profile, media, prefixes)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _absolute_path(value: str, name: str) -> str:
    if not pathlib.PurePosixPath(value).is_absolute():
        raise ValueError(f"{name} must be absolute")
    return value


def _int_range(value: Any, name: str, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < lo or value > hi:
        raise ValueError(f"{name} must be an integer in [{lo}, {hi}]")
    return value
