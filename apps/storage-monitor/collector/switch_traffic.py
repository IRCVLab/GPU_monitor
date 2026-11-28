"""Nexus network throughput, sampled from the storage switch's RouterOS API.

One daemon thread polls the byte counters of the configured switch interfaces,
turns counter deltas into rates, and keeps 1-minute buckets for the last 24 h.
Rates are from the attached server's side: ``rx`` is traffic the server receives
(the switch interface's tx counter) and ``tx`` is traffic the server sends.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
import re
import threading
import time
from typing import Callable, Mapping

from collector.ids import SERVER_ID_RE
from collector.routeros import RouterOSError, connect
from collector.store import _reject_symlinks, _write_atomic

_LOG = logging.getLogger(__name__)

SAMPLE_SECONDS = 2
MAX_GAP_SECONDS = 10
STALE_SECONDS = 30
WINDOW_SECONDS = 24 * 3600
BUCKET_SECONDS = 60
PERSIST_SECONDS = 60
BACKOFF_MAX_SECONDS = 30
SCHEMA = "storage-viz.network/1"
STATE_FILE = "network.json"
MAX_STATE_BYTES = 4 * 1024 * 1024
COUNTER_QUERY = ["/interface/print", "=stats=", "=.proplist=name,rx-byte,tx-byte"]
INTERFACE_RE = re.compile(r"[A-Za-z0-9_.@/-]{1,64}")
HOST_RE = re.compile(r"[A-Za-z0-9_.-]{1,253}")


@dataclass(frozen=True)
class LinkSpec:
    server_id: str
    interface: str
    capacity_mbps: int

    @property
    def key(self) -> str:
        return f"{self.server_id}:{self.interface}"


@dataclass(frozen=True)
class NetworkConfig:
    host: str
    port: int
    user: str
    secret_file: Path
    links: tuple[LinkSpec, ...]


def parse_config(env: Mapping[str, str]) -> NetworkConfig | None:
    """None when the feature is off. A half-configured or malformed setting raises ValueError."""
    names = {name: env.get(f"STORAGE_VIZ_NETWORK_{name}", "").strip() for name in ("SWITCH", "SECRET_FILE", "USER", "LINKS")}
    if not any(names.values()):
        return None
    for required in ("SWITCH", "SECRET_FILE", "LINKS"):
        if not names[required]:
            raise ValueError(f"STORAGE_VIZ_NETWORK_{required} is required when the network card is configured")
    host, _, port_text = names["SWITCH"].rpartition(":")
    if not host:
        host, port_text = names["SWITCH"], "8728"
    if not HOST_RE.fullmatch(host) or not port_text.isdigit() or not 0 < int(port_text) < 65536:
        raise ValueError("STORAGE_VIZ_NETWORK_SWITCH must be host or host:port")
    secret_file = Path(names["SECRET_FILE"])
    if not secret_file.is_absolute():
        raise ValueError("STORAGE_VIZ_NETWORK_SECRET_FILE must be an absolute path")
    user = names["USER"] or "storage-viz"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", user):
        raise ValueError("STORAGE_VIZ_NETWORK_USER has unsupported characters")
    links = []
    for item in names["LINKS"].split(","):
        parts = item.strip().split(":")
        if len(parts) != 3:
            raise ValueError(f"STORAGE_VIZ_NETWORK_LINKS entry {item.strip()!r} must be server_id:interface:capacity_mbps")
        server_id, interface, capacity = parts
        if not SERVER_ID_RE.fullmatch(server_id) or server_id in {".", ".."}:
            raise ValueError(f"STORAGE_VIZ_NETWORK_LINKS server id {server_id!r} is not valid")
        if not INTERFACE_RE.fullmatch(interface):
            raise ValueError(f"STORAGE_VIZ_NETWORK_LINKS interface {interface!r} is not valid")
        if not capacity.isdigit() or int(capacity) <= 0:
            raise ValueError(f"STORAGE_VIZ_NETWORK_LINKS capacity {capacity!r} must be a positive number of Mb/s")
        spec = LinkSpec(server_id, interface, int(capacity))
        if any(spec.key == other.key for other in links):
            raise ValueError(f"STORAGE_VIZ_NETWORK_LINKS lists {spec.key} twice")
        links.append(spec)
    return NetworkConfig(host, int(port_text), user, secret_file, tuple(links))


class LinkTraffic:
    """Rates and 24 h buckets for one link. Not thread-safe; ``SwitchTraffic`` holds the lock."""

    def __init__(self, spec: LinkSpec) -> None:
        self.spec = spec
        # minute start (unix) -> [samples, rx_sum, rx_max, tx_sum, tx_max], in bits per second
        self.buckets: dict[int, list[float]] = {}
        self.last: tuple[float, int, int] | None = None
        self.now: tuple[float, float, float] | None = None
        self.error = False

    def add(self, rx_bytes: int, tx_bytes: int, mono: float, unix: float) -> None:
        previous, self.last = self.last, (mono, rx_bytes, tx_bytes)
        if previous is None:
            return
        gap = mono - previous[0]
        if gap <= 0 or gap > MAX_GAP_SECONDS or rx_bytes < previous[1] or tx_bytes < previous[2]:
            return
        rx, tx = (rx_bytes - previous[1]) * 8 / gap, (tx_bytes - previous[2]) * 8 / gap
        self.now = (rx, tx, unix)
        minute = int(unix) // BUCKET_SECONDS * BUCKET_SECONDS
        bucket = self.buckets.setdefault(minute, [0, 0.0, 0.0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += rx
        bucket[2] = max(bucket[2], rx)
        bucket[3] += tx
        bucket[4] = max(bucket[4], tx)
        self.prune(unix)

    def prune(self, unix: float) -> None:
        oldest = int(unix) - WINDOW_SECONDS
        for minute in [m for m in self.buckets if m + BUCKET_SECONDS <= oldest]:
            del self.buckets[minute]

    def view(self, unix: float) -> dict:
        self.prune(unix)
        minutes = sorted(self.buckets)
        rows = [[m, round(self.buckets[m][1] / self.buckets[m][0]), round(self.buckets[m][2]),
                 round(self.buckets[m][3] / self.buckets[m][0]), round(self.buckets[m][4])] for m in minutes]
        peak = None
        if minutes:
            rx_at = max(minutes, key=lambda m: self.buckets[m][2])
            tx_at = max(minutes, key=lambda m: self.buckets[m][4])
            peak = {"rx_bps": round(self.buckets[rx_at][2]), "rx_unix": rx_at,
                    "tx_bps": round(self.buckets[tx_at][4]), "tx_unix": tx_at}
        now = None if self.now is None else {"rx_bps": round(self.now[0]), "tx_bps": round(self.now[1]), "unix": int(self.now[2])}
        fresh = now is not None and unix - now["unix"] <= STALE_SECONDS
        return {"server_id": self.spec.server_id, "interface": self.spec.interface,
                "capacity_bps": self.spec.capacity_mbps * 1_000_000, "now": now, "peak": peak, "buckets": rows,
                "state": "ok" if fresh else "error" if self.error else "stale"}


class SwitchTraffic:
    def __init__(self, config: NetworkConfig, state_dir: Path | None = None, clock=time,
                 connector: Callable[[], object] | None = None) -> None:
        self.config = config
        self.clock = clock
        self._connector = connector or self._connect
        self._path = None if state_dir is None else Path(state_dir) / STATE_FILE
        self._links = {spec.key: LinkTraffic(spec) for spec in config.links}
        self._lock = threading.Lock()
        self._client = None
        self._backoff = SAMPLE_SECONDS
        self._failing = False
        self._saved = clock.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load()

    def _connect(self):
        password = self.config.secret_file.read_text(encoding="utf-8").strip()
        return connect(self.config.host, self.config.port, self.config.user, password)

    def snapshot(self) -> dict:
        unix = self.clock.time()
        with self._lock:
            return {"links": [link.view(unix) for link in self._links.values()], "sample_seconds": SAMPLE_SECONDS,
                    "generated_unix": int(unix)}

    def poll_once(self) -> float:
        """Take one sample and return the seconds to wait before the next."""
        try:
            if self._client is None:
                self._client = self._connector()
            rows = self._client.talk(COUNTER_QUERY)
            mono, unix = self.clock.monotonic(), self.clock.time()
        except (OSError, ValueError, RouterOSError) as exc:
            self._drop_client()
            self._backoff = min(self._backoff * 2, BACKOFF_MAX_SECONDS) if self._failing else SAMPLE_SECONDS
            if not self._failing:
                _LOG.warning("switch traffic poll failed: %s: %s", type(exc).__name__, exc)
            self._failing = True
            with self._lock:
                for link in self._links.values():
                    link.error = True
            return self._backoff
        if self._failing:
            _LOG.info("switch traffic poll recovered")
        self._failing, self._backoff = False, SAMPLE_SECONDS
        self._ingest(rows, mono, unix)
        if mono - self._saved >= PERSIST_SECONDS:
            self._save(mono)
        return SAMPLE_SECONDS

    def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def _ingest(self, rows: list[dict[str, str]], mono: float, unix: float) -> None:
        by_name = {row.get("name"): row for row in rows}
        with self._lock:
            for link in self._links.values():
                row = by_name.get(link.spec.interface)
                try:
                    # The switch counts from its own side: its tx is what the attached server receives.
                    rx, tx = int(row["tx-byte"]), int(row["rx-byte"])
                except (TypeError, KeyError, ValueError):
                    link.error = True
                    continue
                link.error = False
                link.add(rx, tx, mono, unix)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="switch-traffic", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._drop_client()
        self._save(self.clock.monotonic())

    def _run(self) -> None:
        delay = 0.0
        while not self._stop.wait(delay):
            try:
                delay = self.poll_once()
            except Exception:
                _LOG.exception("switch traffic poll crashed")
                delay = BACKOFF_MAX_SECONDS

    def _save(self, mono: float) -> None:
        self._saved = mono
        if self._path is None:
            return
        with self._lock:
            links = {key: [[m, int(b[0]), round(b[1]), round(b[2]), round(b[3]), round(b[4])] for m, b in sorted(link.buckets.items())]
                     for key, link in self._links.items()}
        data = json.dumps({"schema": SCHEMA, "links": links}, separators=(",", ":"), sort_keys=True).encode()
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _reject_symlinks(self._path.parent, self._path)
            _write_atomic(self._path, data)
        except (OSError, ValueError) as exc:
            _LOG.warning("switch traffic history not saved: %s", type(exc).__name__)

    def _load(self) -> None:
        if self._path is None:
            return
        unix = self.clock.time()
        try:
            _reject_symlinks(self._path.parent, self._path)
            if not self._path.exists() or self._path.stat().st_size > MAX_STATE_BYTES:
                return
            raw = json.loads(self._path.read_bytes())
            if raw["schema"] != SCHEMA:
                return
            saved = raw["links"]
            for key, link in self._links.items():
                for item in saved.get(key, []):
                    parsed = _parse_bucket(item, unix)
                    if parsed:
                        link.buckets[parsed[0]] = parsed[1:]
                link.prune(unix)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            _LOG.warning("switch traffic history not loaded: %s", type(exc).__name__)
            for link in self._links.values():
                link.buckets.clear()


def _parse_bucket(item, unix: float):
    if not isinstance(item, list) or len(item) != 6:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in item):
        return None
    minute, count, rx_sum, rx_max, tx_sum, tx_max = item
    if minute % BUCKET_SECONDS or count < 1 or minute > unix + BUCKET_SECONDS:
        return None
    if rx_max * count < rx_sum - count or tx_max * count < tx_sum - count:
        return None
    return int(minute), int(count), float(rx_sum), float(rx_max), float(tx_sum), float(tx_max)
