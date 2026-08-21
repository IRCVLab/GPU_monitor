#!/usr/bin/env python3.12
"""Validate and exec the managed storage-viz direct proxy target."""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Callable, Sequence

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class LauncherError(RuntimeError):
    pass


@dataclass(frozen=True)
class LauncherConfig:
    release_root: Path = Path("/srv/storage-viz-dashboard/releases")
    state_path: Path = Path("/var/lib/storage-viz-dashboard/activation-state.json")

    def __post_init__(self) -> None:
        object.__setattr__(self, "release_root", Path(self.release_root))
        object.__setattr__(self, "state_path", Path(self.state_path))


def _state(config: LauncherConfig) -> dict[str, object]:
    try:
        raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LauncherError(f"activation state is required: {exc}") from exc
    if not isinstance(raw, dict):
        raise LauncherError("activation state must be an object")
    return raw


def _immutable_file(path: Path) -> Path:
    if path.is_symlink():
        raise LauncherError("proxy target must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("proxy target is missing or broken") from exc
    if resolved.name != "direct_proxy.py" or resolved.is_symlink() or not resolved.is_file():
        raise LauncherError("proxy target must be direct_proxy.py")
    mode = resolved.stat().st_mode
    if mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise LauncherError("proxy target must be immutable/non-writable")
    return resolved


def _active_release_target(resolved: Path, config: LauncherConfig, state: dict[str, object]) -> bool:
    release_root = config.release_root.resolve(strict=False)
    try:
        rel = resolved.relative_to(release_root)
    except ValueError:
        return False
    parts = rel.parts
    if len(parts) != 4 or not SHA_RE.fullmatch(parts[0]) or parts[1:] != ("storage-monitor", "deploy", "direct_proxy.py"):
        return False
    active_release = state.get("release")
    return isinstance(active_release, str) and resolved.parents[1] == Path(active_release).resolve(strict=False)


def _legacy_target(resolved: Path, state: dict[str, object]) -> bool:
    legacy_backup = state.get("legacy_backup")
    if not isinstance(legacy_backup, str) or not legacy_backup:
        return False
    expected = Path(legacy_backup).resolve(strict=False) / "deploy/direct_proxy.py"
    return resolved == expected


def validate_proxy_target(target: str | Path, config: LauncherConfig = LauncherConfig()) -> Path:
    resolved = _immutable_file(Path(target))
    state = _state(config)
    if _active_release_target(resolved, config, state) or _legacy_target(resolved, state):
        return resolved
    raise LauncherError("proxy target is not the active storage release or recorded legacy backup")


def launch(argv: Sequence[str], *, config: LauncherConfig = LauncherConfig(), execv: Callable[[str, list[str]], object] = os.execv) -> None:
    if not argv:
        raise LauncherError("direct_proxy.py target argument is required")
    target = validate_proxy_target(argv[0], config)
    execv(sys.executable, [sys.executable, str(target), *list(argv[1:])])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", default="/srv/storage-viz-dashboard/releases")
    parser.add_argument("--state-path", default="/var/lib/storage-viz-dashboard/activation-state.json")
    parser.add_argument("target")
    args, rest = parser.parse_known_args(argv)
    launch([args.target, *rest], config=LauncherConfig(Path(args.release_root), Path(args.state_path)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LauncherError as exc:
        print(f"storage-viz proxy launcher: {exc}", file=sys.stderr)
        raise SystemExit(2)
