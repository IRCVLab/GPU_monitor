"""Test helpers: a throwaway signing key and agent bundles built like CI builds them."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

APP_ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "storage-viz-agent"
IDENTITY = "storage-viz-release"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_agent_release", APP_ROOT / "deploy/build-agent-release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SigningKey:
    """An ed25519 key made for one test, never a real release key."""

    def __init__(self, directory: Path, name: str = "release") -> None:
        self.private = directory / name
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test-only", "-f", str(self.private)], check=True)
        public = (directory / f"{name}.pub").read_text().split()
        self.allowed_signers_line = f'{IDENTITY} namespaces="{NAMESPACE}" {public[0]} {public[1]}\n'

    def write_allowed_signers(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.allowed_signers_line)
        return path

    def sign(self, data: bytes, namespace: str = NAMESPACE) -> bytes:
        done = subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(self.private), "-n", namespace],
                              input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return done.stdout


def release_files(entry: bytes = b"#!/bin/sh\nexit 0\n") -> Dict[str, Tuple[bytes, int]]:
    return {
        "bin/storage-viz-agent": (entry, 0o755),
        "bin/hstscan": (b"\x7fELF-not-really", 0o755),
        "agent/__init__.py": (b"", 0o644),
        "agent/scan_runner.py": (b"# stand-in\n", 0o644),
    }


def bundle(
    sha: str,
    *,
    release_seq: int = 100,
    floor: int = 80,
    bootstrap_version: int = 1,
    files: Optional[Dict[str, Tuple[bytes, int]]] = None,
    edit_manifest: Optional[Callable[[dict], None]] = None,
    extra_members: Optional[Callable[[tarfile.TarFile], None]] = None,
) -> bytes:
    """A gzip tar laid out like build-agent-release.py output."""
    files = dict(files if files is not None else release_files())
    manifest = {
        "application": "storage-viz-agent",
        "source_sha": sha,
        "release_seq": release_seq,
        "rollback_floor_seq": floor,
        "bootstrap_version": bootstrap_version,
        "files": {path: hashlib.sha256(data).hexdigest() for path, (data, _mode) in files.items()},
    }
    if edit_manifest:
        edit_manifest(manifest)
    files["RELEASE-MANIFEST.json"] = (json.dumps(manifest).encode(), 0o644)
    if extra_members is None:
        return load_builder().pack(files)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, (data, mode) in sorted(files.items()):
            info = tarfile.TarInfo(path)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        extra_members(tar)
    return buffer.getvalue()
