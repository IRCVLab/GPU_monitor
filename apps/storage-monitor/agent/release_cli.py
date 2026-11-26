"""Entry point of an agent release: run, check-config and selftest.

`bin/storage-viz-agent` in a release bundle calls `main`. The bootstrap tier
uses the three commands: the scan units call `run`, the gate's `observe` calls
`check-config` for agent health, and the updater calls `selftest` under the
scan unit's sandbox before it activates a release.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence

from agent.storage_viz_agent import runner
from agent.storage_viz_agent.config import load_config
from agent.storage_viz_agent.capture import read

DEFAULT_CONFIG = "/etc/storage-viz/agent.yaml"
SELFTEST_TIMEOUT_SECONDS = 120
# A fixed tree: (relative path, bytes). Directories come from the paths.
SELFTEST_FILES = (
    ("a/f1", 10000),
    ("a/b/f2", 20000),
    ("c/f3", 1),
    ("f4", 0),
)
SELFTEST_DIRS = 4  # the tree root, a, a/b and c


def _du_bytes(root: pathlib.Path) -> int:
    """Allocated bytes under root, counting each inode once, as du does."""
    seen = set()
    total = 0
    for directory, dirnames, filenames in os.walk(str(root)):
        for name in [""] + dirnames + filenames:
            st = os.lstat(os.path.join(directory, name)) if name else os.lstat(directory)
            if (st.st_dev, st.st_ino) in seen:
                continue
            seen.add((st.st_dev, st.st_ino))
            total += st.st_blocks * 512
    return total


def _make_tree(root: pathlib.Path) -> None:
    for rel, size in SELFTEST_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # Varied bytes so no filesystem can deduplicate or compress them away.
        path.write_bytes(bytes((i * 31 + 7) % 251 for i in range(size)))


def selftest(config_path: str, release_root: pathlib.Path) -> List[str]:
    """Return the list of failures; empty means the release works here."""
    try:
        load_config(config_path)
    except (OSError, ValueError) as exc:
        return [f"config: {exc}"]
    hstscan = release_root / "bin" / "hstscan"
    if not os.access(str(hstscan), os.X_OK):
        return [f"hstscan is not executable: {hstscan}"]
    with tempfile.TemporaryDirectory(prefix="storage-viz-selftest.") as tmp:
        tree = pathlib.Path(tmp) / "tree"
        out = pathlib.Path(tmp) / "out.json"
        _make_tree(tree)
        try:
            completed = subprocess.run(
                [str(hstscan), "--threads", "1", "--out", str(out), str(tree)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=SELFTEST_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"hstscan did not run: {type(exc).__name__}"]
        if completed.returncode != 0:
            return [f"hstscan exited {completed.returncode}: {completed.stderr.decode(errors='replace')[-400:]}"]
        try:
            mounts = json.loads(out.read_text(encoding="utf-8"))["mounts"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return [f"hstscan output unreadable: {exc}"]
        if not isinstance(mounts, list) or len(mounts) != 1 or not isinstance(mounts[0], dict):
            return ["hstscan output must hold exactly one mount"]
        expected: Dict[str, int] = {
            "scanned_files": len(SELFTEST_FILES),
            "scanned_dirs": SELFTEST_DIRS,
            "scanned_bytes": _du_bytes(tree),
            "errors": 0,
        }
        return [
            f"{key}: expected {value}, got {mounts[0].get(key)!r}"
            for key, value in expected.items()
            if mounts[0].get(key) != value
        ]


def check_config(config_path: str) -> Optional[str]:
    """Return an error when this release cannot run with the host config."""
    try:
        cfg = load_config(config_path)
    except (OSError, ValueError) as exc:
        return f"config: {exc}"
    if not os.access(cfg.scanner_path, os.X_OK):
        return f"scanner_path is not executable: {cfg.scanner_path}"
    return None


def main(argv: Sequence[str], release_root: str) -> int:
    parser = argparse.ArgumentParser(prog="storage-viz-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="scan the due groups and publish")
    run.add_argument("--config", default=DEFAULT_CONFIG)
    run.add_argument("--scheduled", action="store_true")
    run.add_argument("--trigger", choices=("timer", "manual"))
    for name in ("check-config", "selftest"):
        commands.add_parser(name).add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args(list(argv))

    if args.command == "run":
        return runner.run_once(args.config, args.scheduled or args.trigger == "timer",
                               read(pathlib.Path(release_root) / "RELEASE-MANIFEST.json", {}))
    if args.command == "check-config":
        error = check_config(args.config)
        print(error or "ok", file=sys.stderr if error else sys.stdout)
        return 1 if error else 0
    failures = selftest(args.config, pathlib.Path(release_root))
    for failure in failures:
        print(f"selftest: {failure}", file=sys.stderr)
    if not failures:
        print("selftest ok")
    return 1 if failures else 0
