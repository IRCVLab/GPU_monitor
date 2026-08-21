#!/usr/bin/env python3.12
"""Validate and exec the managed storage-viz direct proxy target."""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
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
    app_path: Path = Path("/opt/storage-viz-dashboard")

    def __post_init__(self) -> None:
        object.__setattr__(self, "release_root", Path(self.release_root))
        object.__setattr__(self, "state_path", Path(self.state_path))
        object.__setattr__(self, "app_path", Path(self.app_path))


def _state(config: LauncherConfig) -> dict[str, object]:
    try:
        raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LauncherError(f"activation state is required: {exc}") from exc
    if not isinstance(raw, dict):
        raise LauncherError("activation state must be an object")
    return raw


def _regular_file(path: Path) -> Path:
    if path.is_symlink():
        raise LauncherError("proxy target must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("proxy target is missing or broken") from exc
    if resolved.name != "direct_proxy.py" or resolved.is_symlink() or not resolved.is_file():
        raise LauncherError("proxy target must be direct_proxy.py")
    return resolved


def _immutable_file(path: Path) -> Path:
    resolved = _regular_file(path)
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


def _safe_legacy_app(app_path: Path) -> Path:
    if app_path.is_symlink():
        raise LauncherError("recorded legacy app path must not be a symlink")
    try:
        app_stat = app_path.stat()
        canonical = app_path.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("recorded legacy app path is missing or broken") from exc
    if not stat.S_ISDIR(app_stat.st_mode):
        raise LauncherError("recorded legacy app path must be a directory")
    if app_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID):
        raise LauncherError("recorded legacy app path has unsafe mode")
    owner = app_stat.st_uid
    required = (
        canonical / "viewer",
        canonical / "viewer/serve.py",
        canonical / "deploy",
        canonical / "deploy/direct_proxy.py",
    )
    for path in required:
        if path.is_symlink():
            raise LauncherError(f"recorded legacy path must not be a symlink: {path}")
        try:
            path_stat = path.stat()
            path.resolve(strict=True).relative_to(canonical)
        except (OSError, ValueError) as exc:
            raise LauncherError(f"recorded legacy path is missing, broken, or external: {path}") from exc
        expected_directory = path.name in {"viewer", "deploy"}
        if expected_directory and not stat.S_ISDIR(path_stat.st_mode):
            raise LauncherError(f"recorded legacy path must be a directory: {path}")
        if not expected_directory and not stat.S_ISREG(path_stat.st_mode):
            raise LauncherError(f"recorded legacy path must be a regular file: {path}")
        if not expected_directory and not path_stat.st_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
            raise LauncherError(f"recorded legacy script is not readable: {path}")
        if path_stat.st_uid != owner:
            raise LauncherError(f"recorded legacy path owner differs from app root: {path}")
        if path_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID):
            raise LauncherError(f"recorded legacy path has unsafe mode: {path}")
    return canonical


def _legacy_target(resolved: Path, config: LauncherConfig, state: dict[str, object]) -> bool:
    restored = state.get("restored_legacy_target")
    managed = state.get("managed_legacy_proxy_target")
    protected = state.get("protected_legacy_backup")
    if (
        state.get("status") != "rolled_back"
        or not isinstance(restored, str)
        or not isinstance(managed, str)
    ):
        return False
    try:
        canonical_app = _safe_legacy_app(config.app_path)
    except LauncherError:
        return False
    if restored != str(canonical_app):
        return False
    expected = canonical_app / "deploy/direct_proxy.py"
    if managed != str(expected) or resolved != expected:
        return False
    if protected is None:
        return True
    if not isinstance(protected, str):
        return False
    backup_proxy = Path(protected).resolve(strict=False) / "deploy/direct_proxy.py"
    if backup_proxy.is_symlink() or not backup_proxy.is_file():
        return False
    return hashlib.sha256(resolved.read_bytes()).digest() == hashlib.sha256(backup_proxy.read_bytes()).digest()


def validate_proxy_target(target: str | Path, config: LauncherConfig = LauncherConfig()) -> Path:
    resolved = _regular_file(Path(target))
    state = _state(config)
    if _active_release_target(resolved, config, state):
        return _immutable_file(resolved)
    if _legacy_target(resolved, config, state):
        return resolved
    raise LauncherError("proxy target is not the active storage release or recorded legacy backup")


def launch(argv: Sequence[str], *, config: LauncherConfig = LauncherConfig(), execv: Callable[[str, list[str]], object] = os.execv) -> None:
    if len(argv) != 1:
        raise LauncherError("exactly one direct_proxy.py target argument is required")
    target = validate_proxy_target(argv[0], config)
    execv(sys.executable, [sys.executable, str(target)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", default="/srv/storage-viz-dashboard/releases")
    parser.add_argument("--state-path", default="/var/lib/storage-viz-dashboard/activation-state.json")
    parser.add_argument("--app-path", default="/opt/storage-viz-dashboard")
    parser.add_argument("target")
    args = parser.parse_args(argv)
    launch(
        [args.target],
        config=LauncherConfig(Path(args.release_root), Path(args.state_path), Path(args.app_path)),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LauncherError as exc:
        print(f"storage-viz proxy launcher: {exc}", file=sys.stderr)
        raise SystemExit(2)
