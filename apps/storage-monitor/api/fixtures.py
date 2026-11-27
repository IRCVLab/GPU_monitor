"""Read-only fleet backed by manifest and volume capture fixtures."""
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from collector.errors import RescanDisabled, UnknownServer
from collector.legacy import legacy_volume
from collector.snapshot import validate_download, DesiredServer
from collector.health import Data, Link
from collector.contract import decode_json, parse_manifest, parse_capture
from collector.ids import SERVER_ID_RE
from collector.inventory import Server as InventoryServer
from collector.store import Record
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
        doc = decode_json(data)
        if isinstance(doc, dict) and doc.get("schema_version") == 1:
            sha = hashlib.sha256(data).hexdigest()
            status = {"server_id": server.id, "generation": f"{server.id}-{doc['scan_started_unix']}-v1.json",
                      "status": "partial" if any(r["status"] in {"partial", "failed"} for r in doc["selected_roots"]) else "complete",
                      "byte_size": len(data), "sha256": sha, "scan_finished_unix": doc["scan_finished_unix"],
                      "config_digest": doc["config_digest"]}
            payload = validate_download(status, data, DesiredServer(server.id, server.scanner_digest)).payload
            manifest, held, blobs = legacy_volume(payload, sha)
            pub = publication(manifest, held, blobs, user_file_totals={u["uid"]: u["files"] for u in payload["users"]})
            mono = self.clock.monotonic()
            return build_view(server, Record(link=Link(state="ok")), pub, ObservationView({"state":"idle"}, mono),
                              (manifest.written_unix, mono), None, "legacy")
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
                          None, "gate")

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
