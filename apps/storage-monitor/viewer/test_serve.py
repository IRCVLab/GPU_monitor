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


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ServeSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="storage-viz-serve-test.")
        self.addCleanup(self.tmp.cleanup)
        data_dir = Path(self.tmp.name)
        (data_dir / "hosts.json").write_text('[{"id":"hinton","label":"hinton","file":"hinton"}]\n', encoding="utf-8")
        (data_dir / "hinton.sample.json").write_text('{"schema_version":1,"hostname":"hinton"}\n', encoding="utf-8")
        self.port = free_port()
        env = os.environ.copy()
        env.update(
            STORAGE_VIZ_DATA_DIR=str(data_dir),
            STORAGE_VIZ_BIND="127.0.0.1",
            STORAGE_VIZ_PORT=str(self.port),
        )
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVE)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._stop)
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
        if getattr(self, "proc", None) and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.communicate(timeout=3)
        for stream_name in ("stdout", "stderr"):
            stream = getattr(self.proc, stream_name, None)
            if stream and not stream.closed:
                stream.close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get_json(self, path: str) -> dict:
        with urlopen(self.url(path), timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_rescan_is_disabled_by_default_and_data_files_are_served(self) -> None:
        caps = self.get_json("/capabilities")
        self.assertEqual(caps["rescan"], False)
        self.assertIn("Manual rescan only", caps["message"])

        status = self.get_json("/rescan-status")
        self.assertEqual(status["supported"], False)
        self.assertEqual(status["scanning"], False)
        self.assertIn("data_file", status)

        with self.assertRaises(HTTPError) as ctx:
            urlopen(Request(self.url("/rescan"), method="POST"), timeout=3)
        self.assertEqual(ctx.exception.code, 503)

        self.assertEqual(self.get_json("/data/hosts.json")[0]["id"], "hinton")
        self.assertEqual(self.get_json("/data/hinton.sample.json")["hostname"], "hinton")


if __name__ == "__main__":
    unittest.main(verbosity=2)
