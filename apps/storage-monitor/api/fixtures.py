"""Read-only fleet backed by v1 fixture snapshots."""
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from collector.errors import RescanDisabled, UnknownServer
from collector.health import Data, LegacyPublication
from collector.ids import SERVER_ID_RE
from collector.inventory import Server as InventoryServer
from collector.store import Record
from collector.view import ServerView, build_view, render_snapshot


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
        snapshot = render_snapshot(self._read(self._files[server.id]))
        manifest = {"status": snapshot.scan_result, "config_digest": server.scanner_digest}
        return build_view(server, Record(data=Data(manifest=LegacyPublication(manifest))), snapshot, None, None, None)

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
