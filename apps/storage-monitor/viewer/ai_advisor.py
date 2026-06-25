"""AI advisor backend helpers.

This module deliberately keeps filesystem inspection server-owned and
metadata-only.  Local model providers should receive sanitized evidence from
these helpers, never paths plus filesystem/shell authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable, Mapping


SYSTEM_ROOTS = {
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
}


@dataclass(frozen=True)
class ReadOnlyInspectionConfig:
    """Bounds for optional live metadata inspection.

    Inspection is disabled by default and only accepts paths under explicit
    allowlisted roots.  The collector returns metadata summaries only.
    """

    enabled: bool = False
    allowed_roots: tuple[Path, ...] = ()
    max_paths: int = 20
    max_depth: int = 1
    max_entries: int = 200


def _env_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def readonly_config_from_env(env: Mapping[str, str] | None = None) -> ReadOnlyInspectionConfig:
    """Build read-only inspection config from environment-like values."""

    values = os.environ if env is None else env
    roots = []
    for raw_root in values.get("STORAGE_VIZ_AI_ALLOWED_ROOTS", "").split(","):
        raw_root = raw_root.strip()
        if not raw_root or "\x00" in raw_root:
            continue
        candidate = Path(raw_root).expanduser()
        if not candidate.is_absolute():
            continue
        roots.append(candidate.resolve(strict=False))
    return ReadOnlyInspectionConfig(
        enabled=_env_enabled(values.get("STORAGE_VIZ_AI_READONLY_INSPECTION")),
        allowed_roots=tuple(dict.fromkeys(roots)),
        max_paths=_positive_int(values.get("STORAGE_VIZ_AI_MAX_PATHS"), 20),
        max_depth=_positive_int(values.get("STORAGE_VIZ_AI_MAX_DEPTH"), 1),
        max_entries=_positive_int(values.get("STORAGE_VIZ_AI_MAX_ENTRIES"), 200),
    )


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def collect_readonly_evidence(
    paths: Iterable[str],
    config: ReadOnlyInspectionConfig | None = None,
) -> dict:
    """Collect bounded metadata summaries for already-selected paths.

    Disabled mode intentionally returns no per-path rejection details so callers
    do not accidentally use this helper as an oracle for arbitrary paths.
    """

    effective = config or readonly_config_from_env()
    if not effective.enabled:
        return {"enabled": False, "items": [], "rejected": []}

    items = []
    rejected = []
    for index, raw_path in enumerate(paths):
        raw = str(raw_path)
        if index >= effective.max_paths:
            rejected.append({"path": raw, "reason": "max-paths"})
            continue
        path, reason = _safe_inspection_path(raw, effective.allowed_roots)
        if reason:
            rejected.append({"path": raw, "reason": reason})
            continue
        items.append(_metadata_for_path(path, effective))
    return {"enabled": True, "items": items, "rejected": rejected}


def _safe_inspection_path(raw_path: str, allowed_roots: tuple[Path, ...]) -> tuple[Path | None, str | None]:
    if "\x00" in raw_path:
        return None, "null-byte"
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return None, "relative"
    absolute = path.resolve(strict=False)
    raw_absolute = Path(os.path.abspath(os.path.expanduser(raw_path)))
    if str(raw_absolute) == "/":
        return None, "root"
    if _is_system_path(raw_absolute):
        return None, "system"
    if _is_top_level_path(raw_absolute):
        return None, "top-level"
    if not allowed_roots:
        return None, "no-allowed-roots"

    raw_inside = _is_under_any_root(raw_absolute, allowed_roots)
    resolved_inside = _is_under_any_root(absolute, allowed_roots)
    if raw_inside and not resolved_inside:
        return None, "symlink-escape"
    if not resolved_inside:
        return None, "outside-allowed-roots"
    return absolute, None


def _is_under_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        with os_error_suppressed():
            path.relative_to(root)
            return True
    return False


class os_error_suppressed:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, _exc, _tb):
        return exc_type in {OSError, ValueError}


def _is_system_path(path: Path) -> bool:
    text = str(path)
    return any(text == root or text.startswith(f"{root}/") for root in SYSTEM_ROOTS)


def _is_top_level_path(path: Path) -> bool:
    return len(path.parts) == 2


def _metadata_for_path(path: Path, config: ReadOnlyInspectionConfig) -> dict:
    try:
        stat = path.stat()
    except OSError as exc:
        return {"path": str(path), "kind": "missing", "error": type(exc).__name__}

    if path.is_dir():
        entry_count, extension_counts, truncated, immediate_file_bytes = _summarize_directory(path, config)
        return {
            "path": str(path),
            "kind": "directory",
            "mode": stat.st_mode & 0o777,
            "mtime": int(stat.st_mtime),
            "entry_count": entry_count,
            "extension_counts": extension_counts,
            "immediate_file_bytes": immediate_file_bytes,
            "truncated": truncated,
        }
    return {
        "path": str(path),
        "kind": "file",
        "mode": stat.st_mode & 0o777,
        "mtime": int(stat.st_mtime),
        "size_bytes": stat.st_size,
        "extension": path.suffix.lower() or "<no_ext>",
    }


def _summarize_directory(path: Path, config: ReadOnlyInspectionConfig) -> tuple[int, dict[str, int], bool, int]:
    counts: dict[str, int] = {}
    entry_count = 0
    immediate_file_bytes = 0
    truncated = False
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry_count >= config.max_entries:
                    truncated = True
                    break
                entry_count += 1
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    key = "<unreadable>"
                else:
                    if entry.is_symlink():
                        key = "<symlink>"
                    elif entry.is_dir(follow_symlinks=False):
                        key = "<dir>"
                    else:
                        immediate_file_bytes += entry_stat.st_size
                        key = Path(entry.name).suffix.lower() or "<no_ext>"
                counts[key] = counts.get(key, 0) + 1
    except OSError:
        truncated = True
    return entry_count, dict(sorted(counts.items())), truncated, immediate_file_bytes
