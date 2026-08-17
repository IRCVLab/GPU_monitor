import json
import unittest
from unittest.mock import patch

from backend.slack_storage import (
    StorageSummaryError,
    build_storage_command_payload,
    fetch_storage_summary,
)


def _mount(
    path: str,
    *,
    used_pct: int,
    available: int,
    media: str = "ssd",
    mount_id: str | None = None,
) -> dict:
    total = 1024**4
    mount = {
        "path": path,
        "df_total": total,
        "df_used": total - available,
        "df_avail": available,
        "df_use_pct": used_pct,
        "storage_media": media,
        "storage_media_confidence": "resolved",
    }
    if mount_id is not None:
        mount["mount_id"] = mount_id
    return mount


def _server(
    server_id: str,
    name: str,
    *,
    order: int,
    mounts: list[dict] | None = None,
    freshness: str = "fresh",
    pull_status: str = "succeeded",
    selected_roots: list[dict] | None = None,
) -> dict:
    mount_rows = mounts or []
    return {
        "id": server_id,
        "display_name": name,
        "order": order,
        "snapshot_availability": "available",
        "freshness": freshness,
        "latest_pull_status": pull_status,
        "latest_scan_result": "complete",
        "configuration_sync": "in_sync",
        "mount_count": len(mount_rows),
        "overview_snapshot": {
            "server_id": server_id,
            "mounts": mount_rows,
            "selected_roots": selected_roots or [],
        },
        "active_job": None,
    }


def _section_texts(payload: dict) -> list[str]:
    return [
        block["text"]["text"]
        for block in payload["blocks"]
        if block["type"] == "section"
    ]


class SlackStoragePayloadTests(unittest.TestCase):
    def test_overview_preserves_storage_api_registration_order(self) -> None:
        data = {
            "data_mode": "inventory",
            "servers": [
                _server("first", "First", order=30),
                _server("second", "Second", order=10, freshness="stale"),
                _server("third", "Third", order=20),
            ],
        }

        payload = build_storage_command_payload(data, "")
        texts = _section_texts(payload)

        self.assertEqual(
            [next(name for name in ("First", "Second", "Third") if name in text) for text in texts],
            ["First", "Second", "Third"],
        )

    def test_overview_uses_compact_mount_capacity_cues(self) -> None:
        data = {
            "data_mode": "inventory",
            "servers": [
                _server(
                    "poseidon",
                    "Poseidon",
                    order=10,
                    mounts=[
                        _mount(
                            "/home",
                            used_pct=96,
                            available=44_526_391_296,
                            media="ssd",
                        ),
                        _mount(
                            "/data",
                            used_pct=50,
                            available=512 * 1024**3,
                            media="hdd",
                        ),
                    ],
                )
            ],
        }

        payload = build_storage_command_payload(data, "")
        rendered = "\n".join(_section_texts(payload))
        context = " ".join(
            element["text"]
            for block in payload["blocks"]
            if block["type"] == "context"
            for element in block["elements"]
        )

        self.assertIn("Poseidon", rendered)
        self.assertIn("2 volumes", rendered)
        self.assertIn("● /home · 96% · 41G · SSD", rendered)
        self.assertIn("◑ /data · 50% · 512G · HDD", rendered)
        self.assertIn("○ empty", context)
        self.assertIn("● full", context)
        self.assertNotIn("freshness", rendered)
        self.assertNotIn("latest_pull_status", rendered)

    def test_unhealthy_server_does_not_show_stale_mount_capacity(self) -> None:
        data = {
            "data_mode": "inventory",
            "servers": [
                _server(
                    "stale",
                    "Stale Server",
                    order=1,
                    freshness="stale",
                    mounts=[_mount("/data", used_pct=20, available=800 * 1024**3)],
                )
            ],
        }

        payload = build_storage_command_payload(data, "")
        rendered = "\n".join(_section_texts(payload))

        self.assertIn("snapshot stale", rendered.lower())
        self.assertNotIn("/data", rendered)
        self.assertNotIn("800G", rendered)

    def test_server_query_filters_without_reordering(self) -> None:
        data = {
            "data_mode": "inventory",
            "servers": [
                _server("poseidon", "Poseidon", order=1),
                _server("hinton", "Hinton", order=2),
            ],
        }

        payload = build_storage_command_payload(data, "hint")
        rendered = "\n".join(_section_texts(payload))

        self.assertIn("Hinton", rendered)
        self.assertNotIn("Poseidon", rendered)

    def test_root_backed_paths_share_one_physical_capacity_cue(self) -> None:
        available = 141 * 1024**3
        data = {
            "data_mode": "inventory",
            "servers": [
                _server(
                    "turing",
                    "Turing",
                    order=1,
                    mounts=[
                        _mount(
                            "/home",
                            used_pct=98,
                            available=available,
                            mount_id="home-root",
                        ),
                        _mount(
                            "/data",
                            used_pct=98,
                            available=available,
                            mount_id="data-root",
                        ),
                    ],
                    selected_roots=[
                        {"mount_id": "home-root", "capacity_id": "dev-8-2"},
                        {"mount_id": "data-root", "capacity_id": "dev-8-2"},
                    ],
                )
            ],
        }

        payload = build_storage_command_payload(data, "")
        rendered = "\n".join(_section_texts(payload))

        self.assertIn("1 volume", rendered)
        self.assertIn("/home + /data", rendered)
        self.assertEqual(rendered.count("98%"), 1)
        self.assertEqual(rendered.count("141G"), 1)

    def test_major_minor_groups_paths_when_capacity_id_is_absent(self) -> None:
        available = 220 * 1024**3
        data = {
            "data_mode": "inventory",
            "servers": [
                _server(
                    "legacy",
                    "Legacy",
                    order=1,
                    mounts=[
                        _mount(
                            "/home",
                            used_pct=73,
                            available=available,
                            mount_id="home-root",
                        ),
                        _mount(
                            "/data",
                            used_pct=73,
                            available=available,
                            mount_id="data-root",
                        ),
                    ],
                    selected_roots=[
                        {"mount_id": "home-root", "major_minor": "259:4"},
                        {"mount_id": "data-root", "major_minor": "259:4"},
                    ],
                )
            ],
        }

        payload = build_storage_command_payload(data, "")
        rendered = "\n".join(_section_texts(payload))

        self.assertIn("1 volume", rendered)
        self.assertIn("/home + /data", rendered)
        self.assertEqual(rendered.count("73%"), 1)

    def test_long_search_query_keeps_header_within_slack_limit(self) -> None:
        payload = build_storage_command_payload({"servers": []}, "x" * 500)
        header = payload["blocks"][0]["text"]["text"]

        self.assertLessEqual(len(header), 150)
        self.assertTrue(header.endswith("…"))

    def test_detailed_server_output_stays_within_slack_section_limit(self) -> None:
        mounts = [
            _mount(
                f"/{index:02d}-" + "&" * 500,
                used_pct=index,
                available=(100 - index) * 1024**3,
                mount_id=f"mount-{index}",
            )
            for index in range(64)
        ]
        data = {
            "data_mode": "inventory",
            "servers": [_server("dense", "Dense", order=1, mounts=mounts)],
        }

        payload = build_storage_command_payload(data, "dense")
        sections = _section_texts(payload)

        self.assertTrue(all(len(text) <= 3000 for text in sections))
        self.assertIn("omitted", sections[0])

    def test_escaped_server_name_cannot_overflow_slack_section(self) -> None:
        data = {
            "servers": [
                _server(
                    "escaped",
                    "&" * 5000,
                    order=1,
                    mounts=[_mount("/home", used_pct=10, available=900 * 1024**3)],
                )
            ]
        }

        payload = build_storage_command_payload(data, "")

        self.assertTrue(all(len(text) <= 3000 for text in _section_texts(payload)))


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit: int) -> bytes:
        return self._body


class StorageSummaryFetchTests(unittest.TestCase):
    def test_fetch_accepts_bounded_loopback_api_response(self) -> None:
        expected = {"data_mode": "inventory", "servers": []}

        with patch("backend.slack_storage.urlopen", return_value=_FakeResponse(expected)):
            actual = fetch_storage_summary("http://127.0.0.1:8088/api/servers")

        self.assertEqual(actual, expected)

    def test_fetch_rejects_non_loopback_url(self) -> None:
        with self.assertRaises(StorageSummaryError):
            fetch_storage_summary("https://example.com/api/servers")


if __name__ == "__main__":
    unittest.main()
