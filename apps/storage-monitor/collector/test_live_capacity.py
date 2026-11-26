from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from threading import Thread
from types import SimpleNamespace

from collector.live_capacity import LiveCapacity


OBSERVED_AT = "2026-11-25T07:22:00+00:00"
NOW = int(datetime.fromisoformat(OBSERVED_AT).timestamp())
HOST = "166.104.167.11"
DISK = {"capacity_id": "dev-259-4", "mount": "/", "device": "/dev/nvme2n1p2",
        "fs_type": "ext4", "total": 1000, "used": 840, "available": 100, "percent": 90}


def _gpu_row(port, volumes=None, observed_at=OBSERVED_AT):
    return {"host": HOST, "port": port, "observed_at": observed_at,
            "volumes": [DISK] if volumes is None else volumes}


def _snapshot():
    return {
        "scan_generation": "turing-1",
        "selected_roots": [
            {"mount_id": key, "capacity_id": DISK["capacity_id"], "mount_source": source, "fstype": "ext4"}
            for key, source in (("home", DISK["device"]), ("data", DISK["device"]), ("other", "/dev/other"))
        ],
        "mounts": [
            {"mount_id": key, "path": "/" + key, "df_total": 1000, "df_used": 800,
             "df_avail": 140, "df_use_pct": 86, "scanned_bytes": 30}
            for key in ("home", "data", "other")
        ],
        "users": [{"name": "root", "bytes": 30}],
    }


def test_local_http_feed_updates_aliases_and_retains_original_scan(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        payload = {"servers": [_gpu_row(2202, [dict(DISK, used=100)]), _gpu_row(2203)]}
        fetches = 0

        def do_GET(self):
            assert self.path == "/storage/capacity"
            type(self).fetches += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(self.payload).encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    with server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        now = NOW
        feed = LiveCapacity([SimpleNamespace(id="turing", host=HOST, port=2203)],
                            server.server_port, clock=lambda: now)
        original = _snapshot()
        try:
            feed.poll_once()
            for _ in range(10):
                result = feed.project("turing", original)
                assert [(m["df_used"], m["df_use_pct"]) for m in result["mounts"]] == [
                    (840, 90), (840, 90), (800, 86),
                ]
                assert result["mounts"][0]["capacity_observed_unix"] == NOW
                assert result["mounts"][0]["scanned_bytes"] == 30
                assert result["scan_generation"] == original["scan_generation"]
                assert result["users"] == original["users"]
            assert Handler.fetches == 1
            assert original == _snapshot()

            now = NOW + 1
            newer_scan = _snapshot()
            for root in newer_scan["selected_roots"]:
                root["scan_finished_unix"] = NOW - 1
            newer_scan["scan_finished_unix"] = now
            newer_scan["selected_roots"].append(dict(newer_scan["selected_roots"][0], mount_id="skipped", scan_finished_unix=now))
            assert feed.project("turing", newer_scan)["mounts"] == result["mounts"]
            newer_scan["selected_roots"][0]["scan_finished_unix"] = now
            assert feed.project("turing", newer_scan) == newer_scan

            monkeypatch.setattr(Handler, "payload", {"unrecognized": "response"})
            feed.poll_once()
            assert feed.project("turing", original) == result

            monkeypatch.setattr(Handler, "payload", {"servers": []})
            feed.poll_once()
            assert feed.project("turing", original) == original

            monkeypatch.setattr(Handler, "payload", {"servers": [_gpu_row(2203), _gpu_row(2203)]})
            feed.poll_once()
            assert feed.project("turing", original) == original

            monkeypatch.setattr(Handler, "payload", {"servers": [_gpu_row(2203)]})
            feed.poll_once()
            now += 901
            assert feed.project("turing", original) == original
        finally:
            server.shutdown()
            thread.join(timeout=1)
