"""Reuse the GPU collector's timestamped volume readings in Storage API responses."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import time
from typing import Any, Callable, Iterable, Mapping

from collector import judge
from collector.contract import decode_json
from collector.inventory import Server


POLL_SECONDS = 30
MAX_AGE_SECONDS = judge.MAX_CAPACITY_AGE_SECONDS
MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class VolumeCapacity:
    total: int
    used: int
    available: int
    percent: int


@dataclass(frozen=True)
class CapacitySample:
    observed_unix: int
    volumes: dict[tuple[str, str, str], VolumeCapacity]

    @cached_property
    def revision(self) -> bytes:
        rows = [
            (key, entry.total, entry.used, entry.available, entry.percent)
            for key, entry in sorted(self.volumes.items())
        ]
        return hashlib.sha256(json.dumps((self.observed_unix, rows), separators=(",", ":")).encode()).digest()


def _volume_key(capacity_id: Any, device: Any, fs_type: Any) -> tuple[str, str, str] | None:
    if all(isinstance(value, str) and value for value in (capacity_id, device, fs_type)):
        return capacity_id, device, fs_type
    return None


def _parse_sample(row: dict[str, Any]) -> CapacitySample | None:
    observed_at = row.get("observed_at")
    volumes = row.get("volumes")
    if not isinstance(observed_at, str) or not isinstance(volumes, list):
        return None
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed.utcoffset() is None:
            return None
        observed_unix = int(observed.timestamp())
    except (OverflowError, OSError, ValueError):
        return None

    parsed = {}
    keys = Counter()
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        key = _volume_key(volume.get("capacity_id"), volume.get("device"), volume.get("fs_type"))
        if key is None:
            continue
        keys[key] += 1
        values = [volume.get(name) for name in ("total", "used", "available", "percent")]
        if not all(type(value) is int and value >= 0 for value in values):
            continue
        total, used, available, percent = values
        if total and used + available <= total and percent <= 100:
            parsed[key] = VolumeCapacity(total, used, available, percent)
    return CapacitySample(observed_unix, {key: value for key, value in parsed.items() if keys[key] == 1})


class LiveCapacity:
    def __init__(self, servers: Iterable[Server], port: int, *, clock: Callable[[], float] = time.time) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("STORAGE_VIZ_CAPACITY_PORT must be a TCP port")
        self._port = port
        self._clock = clock
        self._targets = {server.id: (server.host.casefold(), server.port) for server in servers}
        self._samples: dict[tuple[str, int], CapacitySample] = {}

    def poll_once(self) -> None:
        connection = HTTPConnection("127.0.0.1", self._port, timeout=2)
        try:
            connection.request("GET", "/storage/capacity")
            with connection.getresponse() as response:
                if response.status != 200:
                    return
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                return
            payload = decode_json(data)
        except (HTTPException, OSError, UnicodeError, ValueError):
            return
        finally:
            connection.close()
        rows = payload.get("servers") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return

        samples = {}
        keys = Counter()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("host"), str) or type(row.get("port")) is not int:
                continue
            key = row["host"].casefold(), row["port"]
            keys[key] += 1
            sample = _parse_sample(row)
            if sample is not None:
                samples[key] = sample
        self._samples = {key: sample for key, sample in samples.items() if keys[key] == 1}

    def sample_for(self, server_id: str) -> CapacitySample | None:
        target = self._targets.get(server_id)
        sample = self._samples.get(target)
        if sample is None or not judge.capacity_sample_current(sample.observed_unix, int(self._clock())):
            return None
        return sample

    def project(self, server_id: str, snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.project_sample(snapshot, self.sample_for(server_id))

    @staticmethod
    def project_sample(snapshot: Mapping[str, Any], sample: CapacitySample | None) -> Mapping[str, Any]:
        if sample is None:
            return snapshot

        keys_by_mount = {}
        scan_times = {}
        mounts = snapshot.get("mounts", [])
        roots = {root["mount_id"]: root for root in snapshot.get("selected_roots", [])}
        for mount in mounts:
            root = roots.get(mount["mount_id"], {})
            key = _volume_key(root.get("capacity_id"), root.get("mount_source"), root.get("fstype"))
            if key not in sample.volumes:
                continue
            finished = root.get("scan_finished_unix")
            if finished is None:
                finished = snapshot.get("scan_finished_unix")
            keys_by_mount[root["mount_id"]] = key
            if isinstance(finished, int):
                scan_times[key] = max(scan_times.get(key, finished), finished)

        projected = []
        for mount in mounts:
            key = keys_by_mount.get(mount["mount_id"])
            live = sample.volumes.get(key) if sample.observed_unix > scan_times.get(key, -1) else None
            if live is not None:
                mount = {
                    **mount,
                    "df_total": live.total,
                    "df_used": live.used,
                    "df_avail": live.available,
                    "df_use_pct": live.percent,
                    "capacity_observed_unix": sample.observed_unix,
                }
            projected.append(mount)
        return {**snapshot, "mounts": projected}
