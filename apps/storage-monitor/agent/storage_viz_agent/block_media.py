"""Pure sysfs block-device media classification.

This module intentionally uses only bounded filesystem reads under sysfs.  It
never shells out and never returns device paths; callers get a stable capacity
identifier plus a coarse media classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Dict, Optional, Set, Tuple


_MAJOR_MINOR_RE = re.compile(r"^(\d{1,10}):(\d{1,10})$")


def _parse_major_minor(major_minor: str) -> Optional[Tuple[str, str]]:
    if not isinstance(major_minor, str):
        return None
    match = _MAJOR_MINOR_RE.match(major_minor)
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2))
    if major <= 0:
        return None
    return f"dev-{major}-{minor}", f"{major}:{minor}"


@dataclass(frozen=True)
class MediaResult:
    capacity_id: Optional[str]
    media: str
    confidence: str


def capacity_id(major_minor: str) -> Optional[str]:
    """Return canonical ``dev-<major>-<minor>`` for a valid block major:minor."""

    parsed = _parse_major_minor(major_minor)
    if parsed is None:
        return None
    return parsed[0]


class BlockMediaResolver:
    """Resolve block media by following bounded sysfs block/slave topology."""

    def __init__(self, sysfs_root: Path = Path("/sys"), max_depth: int = 16, max_nodes: int = 256):
        self.sysfs_root = Path(sysfs_root)
        self.max_depth = max(0, int(max_depth))
        self.max_nodes = max(0, int(max_nodes))
        self._cache: Dict[str, MediaResult] = {}

    def resolve(self, major_minor: str) -> MediaResult:
        """Classify a ``major:minor`` device using sysfs only.

        Discovery failures are deliberately non-fatal and collapse to
        ``unknown/unresolved``.
        """

        parsed = _parse_major_minor(major_minor)
        if parsed is None:
            return MediaResult(None, "unknown", "unresolved")
        cid, canonical_major_minor = parsed
        if canonical_major_minor in self._cache:
            return self._cache[canonical_major_minor]

        result = self._resolve_uncached(canonical_major_minor, cid)
        self._cache[canonical_major_minor] = result
        return result

    def _resolve_uncached(self, major_minor: str, cid: str) -> MediaResult:
        try:
            root = self.sysfs_root.resolve(strict=True)
            start = (self.sysfs_root / "dev" / "block" / major_minor).resolve(strict=True)
        except (OSError, RuntimeError):
            return MediaResult(cid, "unknown", "unresolved")

        if not self._within_block_device(start, root):
            return MediaResult(cid, "unknown", "unresolved")

        leaves, failed = self._walk(start, root, depth=0, active=set(), memo={}, node_count=[0])
        if failed or not leaves:
            return MediaResult(cid, "unknown", "unresolved")
        if leaves == {0}:
            return MediaResult(cid, "ssd", "resolved")
        if leaves == {1}:
            return MediaResult(cid, "hdd", "resolved")
        if leaves == {0, 1}:
            return MediaResult(cid, "mixed", "resolved")
        return MediaResult(cid, "unknown", "unresolved")

    def _walk(
        self,
        path: Path,
        root: Path,
        *,
        depth: int,
        active: Set[Path],
        memo: Dict[Path, Tuple[Set[int], bool]],
        node_count: list,
    ) -> Tuple[Set[int], bool]:
        if depth > self.max_depth:
            return set(), True
        try:
            real = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return set(), True
        if not self._within_block_device(real, root):
            return set(), True
        if real in memo:
            return memo[real]
        if real in active:
            return set(), True

        node_count[0] += 1
        if node_count[0] > self.max_nodes:
            return set(), True

        next_active = set(active)
        next_active.add(real)

        rotational = self._read_rotational(real)

        parent = self._partition_parent(real, root)
        if parent is not None and rotational is None:
            result = self._walk(parent, root, depth=depth + 1, active=next_active, memo=memo, node_count=node_count)
            memo[real] = result
            return result

        slaves = self._slave_paths(real, root)
        if slaves is None:
            result = (set(), True)
            memo[real] = result
            return result
        if slaves:
            leaves: Set[int] = set()
            for slave in slaves:
                child_leaves, failed = self._walk(
                    slave, root, depth=depth + 1, active=next_active, memo=memo, node_count=node_count
                )
                if failed:
                    result = (set(), True)
                    memo[real] = result
                    return result
                leaves.update(child_leaves)
            result = (leaves, False)
            memo[real] = result
            return result

        if rotational in (0, 1):
            result = ({rotational}, False)
            memo[real] = result
            return result
        result = (set(), True)
        memo[real] = result
        return result

    def _read_rotational(self, path: Path) -> Optional[int]:
        try:
            value = (path / "queue" / "rotational").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if value == "0":
            return 0
        if value == "1":
            return 1
        return None

    def _partition_parent(self, path: Path, root: Path) -> Optional[Path]:
        try:
            parent = path.parent.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not self._within_block_device(parent, root):
            return None
        return parent

    def _slave_paths(self, path: Path, root: Path) -> Optional[Tuple[Path, ...]]:
        slaves_dir = path / "slaves"
        try:
            entries = tuple(sorted(slaves_dir.iterdir(), key=lambda entry: entry.name))
        except FileNotFoundError:
            return tuple()
        except OSError:
            return None

        resolved = []
        for entry in entries:
            try:
                target = entry.resolve(strict=True)
            except (OSError, RuntimeError):
                return None
            if not self._within_block_device(target, root):
                return None
            resolved.append(target)
        return tuple(resolved)

    def _block_device_name(self, target: Path, root: Path) -> Optional[str]:
        try:
            relative = target.relative_to(root)
        except ValueError:
            return None
        parts = relative.parts
        if len(parts) >= 2 and parts[0] == "block":
            return parts[1]
        if parts and parts[0] == "devices":
            for index, part in enumerate(parts[:-1]):
                if part == "block":
                    return parts[index + 1]
        if self._within(target, root / "devices"):
            candidate = target.name
            for entry in (self.sysfs_root / "class" / "block" / candidate, self.sysfs_root / "block" / candidate):
                try:
                    if entry.resolve(strict=True) == target:
                        return candidate
                except (OSError, RuntimeError):
                    continue
        return None

    def _specific_block_anchors(self, name: str, root: Path) -> Tuple[Path, ...]:
        anchors = []
        seen = set()
        for entry in (self.sysfs_root / "class" / "block" / name, self.sysfs_root / "block" / name):
            try:
                anchor = entry.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if anchor in seen or self._block_device_name(anchor, root) != name:
                continue
            seen.add(anchor)
            anchors.append(anchor)
        return tuple(anchors)

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _within_block_device(self, path: Path, root: Path) -> bool:
        name = self._block_device_name(path, root)
        if name is None:
            return False
        return any(self._within(path, anchor) for anchor in self._specific_block_anchors(name, root))
