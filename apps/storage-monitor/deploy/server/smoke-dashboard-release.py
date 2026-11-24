#!/usr/bin/env python3
"""Smoke-test the packaged dashboard and proxy on temporary loopback listeners."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from threading import Thread
from urllib.parse import quote
from unittest.mock import patch

from storage_dashboard_release import VIEWER_PAGES, viewer_asset_paths


@contextmanager
def serve(runtime):
    with runtime["Server"](("127.0.0.1", 0), runtime["Handler"]) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join()


def fetch(port: int, path: str) -> bytes:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"packaged dashboard returned HTTP {response.status} for {path}")
        return response.read()
    finally:
        connection.close()


def smoke(root: Path, release: Path) -> None:
    inventory = root / "inventory.json"
    inventory.write_text(json.dumps({"servers": [{
        "id": "release-smoke",
        "display_name": "Release smoke",
        "host": "127.0.0.1",
        "enabled": False,
        "username": "monitoring",
        "identity_file": "/etc/storage-viz/smoke-unused-key",
        "known_hosts_file": "/etc/storage-viz/smoke-unused-known-hosts",
    }]}), encoding="utf-8")
    environment = {
        "STORAGE_VIZ_INVENTORY": str(inventory),
        "STORAGE_VIZ_STATE_DIR": str(root / "state"),
        "STORAGE_VIZ_DATA_DIR": str(root / "data"),
    }
    sys.dont_write_bytecode = True
    with patch.dict(os.environ, environment, clear=True):
        dashboard = runpy.run_path(str(release / "viewer/serve.py"))
        with serve(dashboard) as dashboard_port:
            os.environ["STORAGE_VIZ_PROXY_UPSTREAM_PORT"] = str(dashboard_port)
            proxy = runpy.run_path(str(release / "deploy/direct_proxy.py"))
            with serve(proxy) as proxy_port:
                if json.loads(fetch(proxy_port, "/api/servers")) != {"data_mode": "inventory", "servers": []}:
                    raise RuntimeError("packaged dashboard returned unexpected inventory")
                assets = set()
                for page in VIEWER_PAGES:
                    path = Path(page).name
                    html = fetch(proxy_port, "/" if path == "index.html" else "/" + path)
                    if html != (release / page).read_bytes():
                        raise RuntimeError(f"packaged dashboard returned unexpected bytes for {page}")
                    assets.update(viewer_asset_paths(html))
                for asset in sorted(assets | {"echarts.min.js", "users-chart.js"}):
                    if fetch(proxy_port, "/" + quote(asset)) != (release / "viewer" / asset).read_bytes():
                        raise RuntimeError(f"packaged dashboard returned unexpected bytes for {asset}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    artifact = args.output_dir / f"storage-monitor-dashboard-{args.sha}.tar.gz"
    metadata = artifact.with_name(f"storage-monitor-dashboard-{args.sha}.sha256.json")
    identity = json.loads(metadata.read_text(encoding="utf-8"))
    activator = Path(__file__).with_name("activate-dashboard-release.py")
    with tempfile.TemporaryDirectory(prefix="storage-release-smoke-") as directory:
        root = Path(directory)
        with artifact.open("rb") as source:
            prepared = subprocess.check_output(
                [
                    sys.executable, str(activator), "--prepare-only", "--artifact-stdin",
                    "--sha", args.sha, "--expected-digest", identity["sha256"], "--metadata", str(metadata),
                    "--release-root", str(root / "releases"), "--incoming-dir", str(root / "incoming"),
                    "--lock-path", str(root / "activation.lock"), "--app-path", str(root / "app"),
                    "--state-path", str(root / "activation-state.json"),
                ],
                stdin=source,
                timeout=20,
            )
        release = Path(json.loads(prepared)["candidate_release"])
        smoke(root, release)
    print(f"Packaged Storage dashboard and proxy passed smoke checks for {args.sha}")


if __name__ == "__main__":
    main()
