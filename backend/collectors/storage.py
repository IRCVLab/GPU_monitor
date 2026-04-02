"""Collect storage usage from remote Linux servers."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

STORAGE_TTL_SECONDS = 600

STORAGE_CMD_PROC = r"""python3 - <<'PY'
import json
import os

EXCLUDED_FS = {
    "devtmpfs",
    "tmpfs",
    "proc",
    "sysfs",
    "cgroup",
    "cgroup2",
    "squashfs",
    "overlay",
    "ramfs",
    "debugfs",
    "tracefs",
    "pstore",
    "securityfs",
    "devpts",
    "hugetlbfs",
    "mqueue",
    "configfs",
    "efivarfs",
    "autofs",
}
NETWORK_FS = {"nfs", "cifs", "smb3", "smbfs", "nfs4"}
IGNORED_KEYWORDS = ("/nas", "nas/", "/mnt/nas")
DEVICE_PREFIXES = ("/dev/nvme", "/dev/sd", "/dev/vd", "/dev/xvd", "/dev/mapper/", "/dev/md")
MIN_CAPACITY_BYTES = 5 * 1024 * 1024 * 1024


def iter_mounts():
    seen_mounts = set()
    seen_devices = set()
    with open("/proc/mounts", "r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.split()
            if len(parts) < 3:
                continue
            device, mount_point, fs_type = parts[:3]
            if fs_type in EXCLUDED_FS or fs_type in NETWORK_FS:
                continue
            if mount_point.startswith("/proc") or mount_point.startswith("/sys"):
                continue
            if mount_point.startswith("/run") or mount_point.startswith("/var/run"):
                continue
            lower_mount = mount_point.lower()
            if any(keyword in lower_mount for keyword in IGNORED_KEYWORDS):
                continue
            if mount_point in seen_mounts:
                continue
            if not any(device.startswith(prefix) for prefix in DEVICE_PREFIXES):
                continue
            if device in seen_devices:
                continue
            seen_mounts.add(mount_point)
            seen_devices.add(device)
            yield device, mount_point, fs_type


mounts = []
total_capacity = 0
total_used = 0

for device, mount_point, fs_type in iter_mounts():
    try:
        stat = os.statvfs(mount_point)
    except PermissionError:
        continue

    block_size = stat.f_frsize or stat.f_bsize or 4096
    capacity = block_size * stat.f_blocks
    if capacity < MIN_CAPACITY_BYTES:
        continue

    free = block_size * stat.f_bfree
    available = block_size * stat.f_bavail
    used = capacity - free
    percent = round((used / capacity * 100.0), 1) if capacity else 0.0

    total_capacity += capacity
    total_used += used
    mounts.append(
        {
            "mount": mount_point,
            "device": device,
            "fs_type": fs_type,
            "size": capacity,
            "used": used,
            "available": available,
            "percent": percent,
        }
    )

mounts.sort(key=lambda item: (-item["percent"], item["mount"]))

summary = {
    "mount_count": len(mounts),
    "total": total_capacity,
    "used": total_used,
    "percent": round((total_used / total_capacity * 100.0), 1) if total_capacity else 0.0,
}

print(json.dumps({"summary": summary, "mounts": mounts}))
PY"""


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class StorageMount:
    mount: str
    device: str
    fs_type: str
    size: int
    used: int
    available: int
    percent: float

    def to_dict(self) -> dict:
        return {
            "mount": self.mount,
            "device": self.device,
            "fs_type": self.fs_type,
            "size": self.size,
            "used": self.used,
            "available": self.available,
            "percent": self.percent,
        }


@dataclass
class StorageSummary:
    mount_count: int
    total: int
    used: int
    percent: float

    def to_dict(self) -> dict:
        return {
            "mount_count": self.mount_count,
            "total": self.total,
            "used": self.used,
            "percent": self.percent,
        }


@dataclass
class StorageInfo:
    collected_at: str | None
    summary: StorageSummary
    mounts: list[StorageMount]

    def to_dict(self) -> dict:
        return {
            "collected_at": self.collected_at,
            "summary": self.summary.to_dict(),
            "mounts": [mount.to_dict() for mount in self.mounts],
        }


def parse_storage(raw: str, collected_at: datetime | None = None) -> StorageInfo:
    payload = json.loads(raw)
    raw_summary = payload.get("summary") or {}
    raw_mounts = payload.get("mounts") or []
    mounts = [
        StorageMount(
            mount=str(item.get("mount") or ""),
            device=str(item.get("device") or ""),
            fs_type=str(item.get("fs_type") or ""),
            size=int(item.get("size") or 0),
            used=int(item.get("used") or 0),
            available=int(item.get("available") or 0),
            percent=float(item.get("percent") or 0.0),
        )
        for item in raw_mounts
        if isinstance(item, dict)
    ]
    summary = StorageSummary(
        mount_count=int(raw_summary.get("mount_count") or len(mounts)),
        total=int(raw_summary.get("total") or 0),
        used=int(raw_summary.get("used") or 0),
        percent=float(raw_summary.get("percent") or 0.0),
    )
    return StorageInfo(
        collected_at=(collected_at or _utcnow()).isoformat(),
        summary=summary,
        mounts=mounts,
    )


class StorageCollector:
    """Collect storage info with a long-lived per-server TTL cache."""

    def __init__(self, cache_ttl_seconds: int = STORAGE_TTL_SECONDS) -> None:
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cached_payload: StorageInfo | None = None
        self._cached_at: datetime | None = None

    def get_cached(self) -> StorageInfo | None:
        return self._cached_payload

    def collect(self, ssh_client) -> StorageInfo:
        now = _utcnow()
        if (
            self._cached_payload is not None
            and self._cached_at is not None
            and now - self._cached_at < self.cache_ttl
        ):
            return self._cached_payload

        raw = ssh_client.run(STORAGE_CMD_PROC, timeout=20)
        payload = parse_storage(raw, collected_at=now)
        self._cached_payload = payload
        self._cached_at = now
        return payload
