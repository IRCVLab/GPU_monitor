"""Read-only fleet backed by manifest and volume capture fixtures."""
import json
import os
from pathlib import Path
import random
import time
from typing import Any, Mapping

from collector.errors import RescanDisabled, UnknownServer
from collector.health import Data, Link
from collector.contract import parse_manifest, parse_capture
from collector.ids import SERVER_ID_RE
from collector.inventory import Server as InventoryServer
from collector.store import Record
from collector.switch_traffic import BUCKET_SECONDS, WINDOW_SECONDS, LinkSpec, LinkTraffic
from collector.view import ServerView, build_view, publication, ObservationView


class FixtureFleet:
    """Sample fixtures served through the same views as the production fleet. Rescans are disabled."""
    clock = time

    def __init__(self, sample_dir: str | os.PathLike[str]) -> None:
        raw_root = Path(sample_dir)
        if raw_root.is_symlink():
            raise ValueError("fixture path must be a real directory")
        root = raw_root.resolve()
        if not root.is_dir():
            raise ValueError("fixture path must be a real directory")
        self.root = root
        self._files: dict[str, str] = {}
        self.servers: tuple[InventoryServer, ...] = tuple(self._load_servers())
        self._views = {server.id: self._build_view(server) for server in self.servers}

    def _read(self, filename):
        path = self.root / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing or unsafe fixture: {filename}")
        return json.loads(path.read_bytes())

    def _load_manifest(self) -> list[dict[str, Any]]:
        raw = self._read("hosts.json")
        if not isinstance(raw, list) or not raw:
            raise ValueError("sample manifest must be a non-empty array")
        return raw

    def _load_servers(self) -> list[InventoryServer]:
        rows = self._load_manifest()
        servers: list[InventoryServer] = []
        seen_ids: set[str] = set()
        seen_files: set[str] = set()
        default_count = 0
        for order, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError("sample manifest rows must be objects")
            unknown_keys = set(row) - {"id", "label", "file", "default", "sample_data", "description"}
            if unknown_keys:
                raise ValueError("sample manifest row has unknown keys")
            sid = row.get("id")
            file_token = row.get("file")
            label = row.get("label", sid)
            if not isinstance(sid, str) or not SERVER_ID_RE.fullmatch(sid) or sid in {".", ".."}:
                raise ValueError("sample manifest id is unsafe")
            if sid in seen_ids:
                raise ValueError("duplicate sample server id")
            if not isinstance(file_token, str) or not SERVER_ID_RE.fullmatch(file_token) or file_token in {".", ".."}:
                raise ValueError("sample manifest file token is unsafe")
            if file_token in seen_files:
                raise ValueError("duplicate sample file")
            if row.get("default") is True:
                default_count += 1
            if row.get("sample_data") is not True:
                raise ValueError("sample manifest rows require sample_data true")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("sample manifest label is required")
            seen_ids.add(sid)
            seen_files.add(file_token)
            self._files[sid] = f"{file_token}.json"
            servers.append(InventoryServer(sid, label, order, "localhost", 22, True, "monitoring", Path("/dev/null"), Path("/dev/null"), {"server_id": sid}, "a" * 64))
        if default_count != 1:
            raise ValueError("sample manifest requires exactly one default")
        present_files = {p.name for p in self.root.iterdir() if p.suffix == ".json" and p.name != "hosts.json"}
        if present_files != set(self._files.values()):
            raise ValueError("sample files must exactly match hosts.json")
        return servers

    def _build_view(self, server: InventoryServer) -> ServerView:
        data = self._bytes(self._files[server.id])
        manifest = parse_manifest(data)
        if manifest.server_id != server.id:
            raise ValueError("fixture manifest server mismatch")
        held = {}
        for volume in manifest.volumes:
            if volume.capture:
                sha = volume.capture.sha256
                held[volume.volume_id] = sha, parse_capture(self.read_capture(server.id, volume.volume_id, sha), volume.expected(sha))
        record = Record(link=Link(state="ok"), data=Data(manifest=manifest, accepted={v: pair[0] for v,pair in held.items()}))
        mono = self.clock.monotonic()
        return build_view(server, record, publication(manifest, held), ObservationView({"state":"idle"}, mono), (manifest.written_unix, mono),
                          None)

    def _bytes(self, filename):
        path = self.root / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing or unsafe fixture: {filename}")
        return path.read_bytes()

    def open_capture(self, sid, vid, sha):
        path = self.root / (sha + ".json.gz")
        if path.is_symlink():
            raise ValueError("unsafe fixture capture")
        return path.open("rb")

    def read_capture(self, sid, vid, sha):
        return self._bytes(sha + ".json.gz")

    def lookup(self, server_id: Any) -> InventoryServer:
        if server_id not in self._views:
            raise UnknownServer("unknown sample server")
        return self._views[server_id].server

    def view(self, server_id: Any) -> ServerView:
        self.lookup(server_id)
        return self._views[server_id]

    def views(self) -> list[ServerView]:
        return [self._views[server.id] for server in self.servers]

    def rollout_view(self) -> None:
        """Fixtures carry no agent release."""
        return None

    def request_rescan(self, server_id: Any, actor_name: str) -> dict[str, Any]:
        self.lookup(server_id)
        raise RescanDisabled()


class SampleNetwork:
    """A synthetic Nexus link for sample-data mode: an idle day with transfer bursts up to about 20 Gb/s.

    The day is a pure function of the clock, so a running sample viewer keeps moving
    and every call returns a full 24 h ending at the current minute.
    """
    clock = time

    def __init__(self) -> None:
        self.spec = LinkSpec("nexus", "bond_nas", 200_000)

    @staticmethod
    def _rate(minute_index: int, direction: int) -> tuple[float, float]:
        slot = random.Random(minute_index // 30 * 2 + direction)
        burst = slot.random() < (0.30 if direction == 0 else 0.12)
        peak = slot.uniform(4e9, 21e9) if direction == 0 else slot.uniform(0.5e9, 6e9)
        wobble = random.Random(minute_index * 2 + direction)
        avg = (peak * wobble.uniform(0.82, 1.0) if burst else wobble.uniform(0.01e9, 0.25e9))
        return avg, avg * wobble.uniform(1.0, 1.08)

    def snapshot(self) -> dict:
        unix = self.clock.time()
        link = LinkTraffic(self.spec)
        this_minute = int(unix) // BUCKET_SECONDS
        for index in range(this_minute - WINDOW_SECONDS // BUCKET_SECONDS + 1, this_minute + 1):
            if index % 700 < 7:
                continue  # a short hole, as after a poller restart
            (rx_avg, rx_max), (tx_avg, tx_max) = self._rate(index, 0), self._rate(index, 1)
            link.buckets[index * BUCKET_SECONDS] = [1, rx_avg, rx_max, tx_avg, tx_max]
        link.now = (self._rate(int(unix), 0)[0], self._rate(int(unix), 1)[0], unix)
        return {"links": [link.view(unix)], "sample_seconds": 2, "generated_unix": int(unix)}
