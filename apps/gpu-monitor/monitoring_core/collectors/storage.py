from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .base import ResourceCollector, ResourcePayload

STORAGE_SCRIPT = r"""
python3 - <<'PY'
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
MIN_CAPACITY_BYTES = 5 * 1024 * 1024 * 1024  # ignore partitions smaller than ~5GB


def iter_mounts():
    seen_mounts = set()
    seen_devices = set()
    with open("/proc/mounts", "r", encoding="utf-8") as fp:
        for line in fp:
            parts = line.split()
            if len(parts) < 3:
                continue
            device, mount_point, fs_type = parts[:3]
            if fs_type in EXCLUDED_FS:
                continue
            if fs_type in NETWORK_FS:
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
    percent = 0.0
    if capacity > 0:
        percent = used / capacity * 100.0
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
            "percent": round(percent, 1),
        }
    )

summary = {
    "mount_count": len(mounts),
    "total": total_capacity,
    "used": total_used,
    "percent": round(total_used / total_capacity * 100.0, 1) if total_capacity else 0.0,
}

print(json.dumps({"summary": summary, "mounts": mounts}))
PY
""".strip()


class StorageCollector(ResourceCollector):
    """Collects filesystem usage stats via statvfs."""

    name = "storage"

    def __init__(self, command: str | None = None, cache_ttl_seconds: int = 60):
        self.command = command or STORAGE_SCRIPT
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cache_payload: ResourcePayload | None = None
        self._cache_timestamp: datetime | None = None

    def collect(self, ssh_client) -> ResourcePayload:
        now = datetime.now(timezone.utc)
        if (
            self._cache_payload is not None
            and self._cache_timestamp is not None
            and now - self._cache_timestamp < self.cache_ttl
        ):
            return self._cache_payload

        stdin = stdout = stderr = None
        try:
            stdin, stdout, stderr = ssh_client.exec_command(self.command)
            stdout.channel.settimeout(15.0)
            stderr.channel.settimeout(15.0)
            raw_stdout = stdout.read().decode("utf-8", errors="ignore").strip()
            error_output = stderr.read().decode("utf-8", errors="ignore").strip()
            if not raw_stdout:
                raise RuntimeError(error_output or "storage collector returned empty output")
            payload = self._parse(raw_stdout)
            self._cache_payload = payload
            self._cache_timestamp = now
            return payload
        finally:
            for stream in (stdin, stdout, stderr):
                if stream:
                    try:
                        stream.close()
                    except Exception:  # pragma: no cover - defensive
                        pass

    def _parse(self, payload: str) -> ResourcePayload:
        import json

        return json.loads(payload)
