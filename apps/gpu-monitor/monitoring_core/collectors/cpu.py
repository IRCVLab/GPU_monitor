from __future__ import annotations

from .base import ResourceCollector, ResourcePayload

CPU_SCRIPT = r"""
python3 - <<'PY'
import json
import os
import time


def read_cpu_stat():
    stats = {}
    with open("/proc/stat", "r", encoding="utf-8") as fp:
        for line in fp:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            key = parts[0]
            values = list(map(int, parts[1:8]))
            stats[key] = values
    return stats


def calculate(prev, curr):
    result = {}
    for key in curr:
        if key not in prev:
            continue
        prev_total = sum(prev[key])
        total = sum(curr[key])
        prev_idle = prev[key][3] + prev[key][4]
        idle = curr[key][3] + curr[key][4]

        total_diff = total - prev_total
        idle_diff = idle - prev_idle
        percent = 0.0
        if total_diff > 0:
            percent = (total_diff - idle_diff) / total_diff * 100.0
        result[key] = percent
    return result


first = read_cpu_stat()
time.sleep(0.2)
second = read_cpu_stat()
percentages = calculate(first, second)

try:
    load1, load5, load15 = os.getloadavg()
except Exception:
    load1 = load5 = load15 = 0.0

meminfo = {}
with open("/proc/meminfo", "r", encoding="utf-8") as fp:
    for line in fp:
        parts = line.split(":")
        if len(parts) < 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip().split()[0]
        try:
            meminfo[key] = int(value) * 1024  # kB to bytes
        except ValueError:
            continue

mem_total = meminfo.get("MemTotal", 0)
mem_available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
mem_used = max(mem_total - mem_available, 0)
mem_percent = (mem_used / mem_total * 100.0) if mem_total else 0.0

summary = {
    "cpu_percent": round(percentages.get("cpu", 0.0), 1),
    "load_1": round(load1, 2),
    "load_5": round(load5, 2),
    "load_15": round(load15, 2),
    "core_count": max(len(percentages) - 1, 0),
    "memory": {
        "total": mem_total,
        "used": mem_used,
        "percent": round(mem_percent, 1),
    },
}

print(json.dumps({"summary": summary}))
PY
""".strip()


class CPUCollector(ResourceCollector):
    """Collects CPU utilization metrics using /proc/stat."""

    name = "cpu"

    def __init__(self, command: str | None = None):
        self.command = command or CPU_SCRIPT

    def collect(self, ssh_client) -> ResourcePayload:
        stdin = stdout = stderr = None
        try:
            stdin, stdout, stderr = ssh_client.exec_command(self.command)
            stdout.channel.settimeout(15.0)
            stderr.channel.settimeout(15.0)
            raw_stdout = stdout.read().decode("utf-8", errors="ignore").strip()
            error_output = stderr.read().decode("utf-8", errors="ignore").strip()
            if not raw_stdout:
                raise RuntimeError(error_output or "cpu collector returned empty output")
            return self._parse(raw_stdout)
        finally:
            for stream in (stdin, stdout, stderr):
                if stream:
                    try:
                        stream.close()
                    except Exception:  # pragma: no cover - defensive
                        pass

    def _parse(self, payload: str) -> ResourcePayload:
        import json

        data = json.loads(payload)
        return data
