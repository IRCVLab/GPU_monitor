from __future__ import annotations

import json
from typing import Dict, List

from .base import ResourceCollector, ResourcePayload


class GPUCollector(ResourceCollector):
    """Collects GPU metrics via gpustat."""

    name = "gpu"

    def __init__(self, command: str = "gpustat --json"):
        self.command = command

    def collect(self, ssh_client) -> ResourcePayload:
        stdin = stdout = stderr = None
        try:
            stdin, stdout, stderr = ssh_client.exec_command(self.command)
            stdout.channel.settimeout(10.0)
            stderr.channel.settimeout(10.0)

            raw_stdout = stdout.read().decode("utf-8", errors="ignore")
            _ = stderr.read()

            parsed = json.loads(raw_stdout)
            return self._normalize(parsed)
        finally:
            for stream in (stdin, stdout, stderr):
                if stream:
                    try:
                        stream.close()
                    except Exception:  # pragma: no cover - defensive
                        pass

    def _normalize(self, payload: Dict) -> ResourcePayload:
        gpus: List[Dict] = payload.get("gpus", [])
        normalized = []
        for gpu in gpus:
            memory_total = gpu.get("memory.total", 0) or 0
            memory_used = gpu.get("memory.used", 0) or 0
            normalized.append(
                {
                    "id": gpu.get("index"),
                    "name": gpu.get("name") or gpu.get("product_name"),
                    "memory": {
                        "used": memory_used,
                        "total": memory_total,
                        "percent": (memory_used / memory_total * 100) if memory_total else 0,
                    },
                    "utilization": {
                        "gpu": gpu.get("utilization.gpu", gpu.get("utilization", 0)),
                        "memory": gpu.get("utilization.memory", 0),
                    },
                    "temperature": gpu.get("temperature.gpu"),
                    "power": gpu.get("power.draw"),
                    "processes": [
                        {
                            "pid": proc.get("pid"),
                            "username": proc.get("username"),
                            "command": proc.get("command"),
                            "memory_used": proc.get("memory.used"),
                        }
                        for proc in gpu.get("processes", [])
                    ],
                }
            )

        return {
            "summary": {
                "count": len(normalized),
                "total_memory": sum(gpu["memory"]["total"] for gpu in normalized),
                "total_memory_used": sum(gpu["memory"]["used"] for gpu in normalized),
                "avg_utilization": (
                    sum(gpu["utilization"]["gpu"] or 0 for gpu in normalized) / len(normalized)
                )
                if normalized
                else 0,
            },
            "gpus": normalized,
            "raw": payload,
        }

