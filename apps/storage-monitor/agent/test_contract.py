"""Exercise the immutable files through the unmodified bootstrap gate."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from agent.bootstrap import gate
from agent.bootstrap.test_gate import FakeHost
from agent.storage_viz_agent import runner
from agent.test_runner import line


def real_run(root, binary):
    paths = gate.Paths(root)
    with tempfile.TemporaryDirectory(dir="/dev/shm") as second:
        roots = [root / "disk", Path(second)]
        roots[0].mkdir()
        devices = [r.stat().st_dev for r in roots]
        if devices[0] == devices[1]:
            pytest.skip("two distinct temporary filesystems required")
        for r in roots:
            (r / "project").mkdir()
            (r / "project" / "sample").write_bytes(b"allocated" * 1024)
        text = "\n".join(line("%d:%d" % (os.major(d), os.minor(d)), str(r), n+1)
                         for n, (d, r) in enumerate(zip(devices, roots)))
        config = root / "agent.yaml"
        config.write_text(json.dumps(dict(server_id="contract-host", scanner_path=str(binary),
            data_dir=str(paths.data), run_dir=str(paths.run),
            volume_aliases={str(r):"fixture-%d" % n for n, r in enumerate(roots)})))
        assert runner.run_once(config, mountinfo_reader=lambda: text) == 0
    return paths


def test_gate_reads_real_runner_files(tmp_path):
    if not shutil.which("cc"):
        pytest.skip("a C compiler is required for the real scanner")
    binary = tmp_path / "hstscan"
    subprocess.run(["cc", "-std=gnu11", "-O2", "-pthread", str(Path(__file__).resolve().parents[1] /
                    "scanner/hstscan.c"), "-o", str(binary)], check=True)
    paths = real_run(tmp_path, binary)
    def serve(command):
        stream = io.BytesIO()
        assert gate.serve(command, paths, FakeHost(), io.BytesIO(), stream) == 0
        header, body = stream.getvalue().split(b"\n", 1)
        assert header == ("storage-viz-gate/1 ok %d" % len(body)).encode()
        return body
    raw = serve("get-manifest")
    manifest = json.loads(raw)
    observation = json.loads(serve("observe"))
    assert observation["scan"] == {"state":"idle", "run":None}
    assert observation["manifest"] == dict(seq=manifest["seq"], sha256=hashlib.sha256(raw).hexdigest())
    assert len(manifest["volumes"]) == 2
    for volume in manifest["volumes"]:
        sha = volume["capture"]["sha256"]
        assert hashlib.sha256(serve("get-capture %s %s" % (volume["volume_id"], sha))).hexdigest() == sha
