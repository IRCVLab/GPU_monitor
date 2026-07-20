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


@dataclass(frozen=True)
class MediaResult:
    capacity_id: Optional[str]
    media: str
    confidence: str


def capacity_id(major_minor: str) -> Optional[str]:
    """Return canonical ``dev-<major>-<minor>`` for a valid block major:minor."""

    if not isinstance(major_minor, str):
        return None
    match = _MAJOR_MINOR_RE.match(major_minor)
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2))
    if major <= 0:
        return None
    return f"dev-{major}-{minor}"


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

        cid = capacity_id(major_minor)
        if cid is None:
            return MediaResult(None, "unknown", "unresolved")
        if major_minor in self._cache:
            return self._cache[major_minor]

        result = self._resolve_uncached(major_minor, cid)
        self._cache[major_minor] = result
        return result

    def _resolve_uncached(self, major_minor: str, cid: str) -> MediaResult:
        try:
            block_root = (self.sysfs_root / "block").resolve(strict=True)
            start = (self.sysfs_root / "dev" / "block" / major_minor).resolve(strict=True)
        except OSError:
            return MediaResult(cid, "unknown", "unresolved")

        if not self._within(start, block_root):
            return MediaResult(cid, "unknown", "unresolved")

        leaves, failed = self._walk(start, block_root, depth=0, visited=set(), node_count=[0])
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
        block_root: Path,
        *,
        depth: int,
        visited: Set[Path],
        node_count: list,
    ) -> Tuple[Set[int], bool]:
        if depth > self.max_depth:
            return set(), True
        try:
            real = path.resolve(strict=True)
        except OSError:
            return set(), True
        if not self._within(real, block_root):
            return set(), True
        if real in visited:
            return set(), True
        visited.add(real)
        node_count[0] += 1
        if node_count[0] > self.max_nodes:
            return set(), True

        rotational = self._read_rotational(real)

        parent = self._partition_parent(real, block_root)
        if parent is not None and rotational is None:
            return self._walk(parent, block_root, depth=depth + 1, visited=visited, node_count=node_count)

        slaves = self._slave_paths(real, block_root)
        if slaves is None:
            return set(), True
        if slaves:
            leaves: Set[int] = set()
            for slave in slaves:
                child_leaves, failed = self._walk(slave, block_root, depth=depth + 1, visited=visited, node_count=node_count)
                if failed:
                    return set(), True
                leaves.update(child_leaves)
            return leaves, False

        if rotational in (0, 1):
            return {rotational}, False
        return set(), True

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

    def _partition_parent(self, path: Path, block_root: Path) -> Optional[Path]:
        try:
            parent = path.parent.resolve(strict=True)
        except OSError:
            return None
        if parent == block_root or not self._within(parent, block_root):
            return None
        return parent

    def _slave_paths(self, path: Path, block_root: Path) -> Optional[Tuple[Path, ...]]:
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
            except OSError:
                return None
            if not self._within(target, block_root):
                return None
            resolved.append(target)
        return tuple(resolved)

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
