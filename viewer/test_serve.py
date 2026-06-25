#!/usr/bin/env python3
"""Regression smoke tests for storage-viz's safe local server."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SERVE = ROOT / "viewer" / "serve.py"
GiB = 1024 ** 3


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def sample_snapshot() -> dict:
    return {
        "schema_version": 1,
        "hostname": "hinton",
        "mounts": [
            {"path": "/", "df_use_pct": 50, "df_avail": 100 * GiB, "tree": {"name": "/", "bytes": 1, "children": []}},
            {"path": "/ssd", "df_use_pct": 95, "df_avail": 5 * GiB, "tree": {"name": "/ssd", "bytes": 1, "children": []}},
        ],
        "top_files": [
            {"path": "/home/alice/.cache/pip/wheels/pkg.whl", "bytes": 6 * GiB, "uid": 1000, "owner": "alice", "mtime": 1710000000},
            {"path": "/ssd/alice/run/checkpoint.pt", "bytes": 20 * GiB, "uid": 1000, "owner": "alice", "mtime": 1710000000},
        ],
        "stale": [],
        "blocked": [],
    }


class ServeSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="storage-viz-serve-test.")
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        (self.data_dir / "hosts.json").write_text('[{"id":"hinton","label":"hinton","file":"hinton"}]\n', encoding="utf-8")
        (self.data_dir / "hinton.sample.json").write_text(json.dumps(sample_snapshot()) + "\n", encoding="utf-8")
        self.proc: subprocess.Popen[str] | None = None
        self.addCleanup(self._stop)

    def start_server(self, extra_env: dict[str, str] | None = None) -> None:
        self.port = free_port()
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("STORAGE_VIZ_AI_"):
                env.pop(key, None)
        env.update(
            STORAGE_VIZ_DATA_DIR=str(self.data_dir),
            STORAGE_VIZ_BIND="127.0.0.1",
            STORAGE_VIZ_PORT=str(self.port),
        )
        if extra_env:
            env.update(extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVE)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                self.get_json("/capabilities")
                return
            except Exception:
                if self.proc.poll() is not None:
                    out, err = self.proc.communicate(timeout=1)
                    self.fail(f"serve.py exited early\nstdout={out}\nstderr={err}")
                time.sleep(0.05)
        self.fail("serve.py did not become ready")

    def _stop(self) -> None:
        if getattr(self, "proc", None) and self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.communicate(timeout=3)
        if getattr(self, "proc", None):
            for stream_name in ("stdout", "stderr"):
                stream = getattr(self.proc, stream_name, None)
                if stream and not stream.closed:
                    stream.close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get_json(self, path: str) -> dict:
        with urlopen(self.url(path), timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, body: dict) -> dict:
        req = Request(
            self.url(path),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_rescan_and_ai_are_disabled_by_default_and_data_files_are_served(self) -> None:
        self.start_server()
        caps = self.get_json("/capabilities")
        self.assertEqual(caps["rescan"], False)
        self.assertIn("Manual rescan only", caps["message"])

        ai = self.get_json("/ai/status")
        self.assertEqual(ai["enabled"], False)
        self.assertEqual(ai["model"], "qwen3.6:27b")
        self.assertIn("disabled", ai["message"].lower())

        status = self.get_json("/rescan-status")
        self.assertEqual(status["supported"], False)
        self.assertEqual(status["scanning"], False)
        self.assertIn("data_file", status)

        with self.assertRaises(HTTPError) as ctx:
            urlopen(Request(self.url("/rescan"), method="POST"), timeout=3)
        self.assertEqual(ctx.exception.code, 503)

        with self.assertRaises(HTTPError) as ctx:
            self.post_json("/ai/recommend", {"host_id": "hinton"})
        self.assertEqual(ctx.exception.code, 503)

        self.assertEqual(self.get_json("/data/hosts.json")[0]["id"], "hinton")
        self.assertEqual(self.get_json("/data/hinton.sample.json")["hostname"], "hinton")

    def test_enabled_mock_ai_returns_validated_recommendations_without_path_escape(self) -> None:
        self.start_server({"STORAGE_VIZ_AI_ENABLED": "1", "STORAGE_VIZ_AI_PROVIDER": "mock"})
        status = self.get_json("/ai/status")
        self.assertEqual(status["enabled"], True)
        self.assertEqual(status["provider"], "mock")
        self.assertEqual(status["model"], "qwen3.6:27b")
        self.assertEqual(status["readonly_inspection"], False)

        payload = self.post_json("/ai/recommend", {"host_id": "hinton", "exclusions": [], "max_items": 10})
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["host_id"], "hinton")
        self.assertIn(payload["mode"], {"mock", "rule+llm"})
        categories = {rec["category"] for rec in payload["recommendations"]}
        self.assertIn("pip-cache", categories)
        self.assertTrue(all(rec["target_path"].startswith("/") for rec in payload["recommendations"]))

        hidden = self.post_json("/ai/recommend", {"host_id": "hinton", "exclusions": [{"type": "action", "action": "delete"}]})
        self.assertNotIn("delete", {rec["action"] for rec in hidden["recommendations"]})

        with self.assertRaises(HTTPError) as ctx:
            self.post_json("/ai/recommend", {"host_id": "../hinton"})
        self.assertEqual(ctx.exception.code, 400)

    def test_ai_recommend_request_language_overrides_server_default(self) -> None:
        self.start_server(
            {
                "STORAGE_VIZ_AI_ENABLED": "1",
                "STORAGE_VIZ_AI_PROVIDER": "none",
                "STORAGE_VIZ_AI_OUTPUT_LANGUAGE": "en",
            }
        )

        payload = self.post_json("/ai/recommend", {"host_id": "hinton", "language": "ko", "max_items": 10})

        self.assertEqual(payload["mode"], "rule-only")
        self.assertEqual(payload["output_language"], "ko")
        self.assertIn("추천", payload["summary"]["headline"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
