"""Deterministic mountinfo parsing and local scan-root selection.

This module is intentionally pure for parsing/classification/selection.  The
only live filesystem adapter is ``read_mountinfo``; callers pass its text into
``parse_mountinfo`` before using selection logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class MountEntry:
    mount_id: int
    parent_id: int
    major_minor: str
    root: str
    mountpoint: str
    options: str
    optional_fields: Tuple[str, ...]
    fstype: str
    source: str
    super_options: str


@dataclass(frozen=True)
class MountDecision:
    status: str  # selected, prohibited, unsupported
    reason: str


@dataclass(frozen=True)
class SelectedRoot:
    mountpoint: str
    entry: MountEntry
    source_mount_id: int
    source_mountpoint: str
    reason: str = "selected"


@dataclass(frozen=True)
class SkippedMount:
    mount_id: int
    mountpoint: str
    reason: str
    chosen_mount_id: Optional[int] = None
    entry: Optional[MountEntry] = None


@dataclass(frozen=True)
class SelectionResult:
    selected: List[SelectedRoot]
    skipped: List[SkippedMount]


LOCAL_FSTYPES = frozenset(
    {
        "ext2",
        "ext3",
        "ext4",
        "xfs",
        "btrfs",
        "zfs",
        "f2fs",
        "jfs",
        "reiserfs",
        "nilfs2",
        "vfat",
        "exfat",
        "ntfs",
        "ntfs3",
    }
)

REMOTE_FSTYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "sshfs",
        "fuse.sshfs",
        "ceph",
        "fuse.ceph",
        "fuse.rclone",
        "fuse.davfs",
        "glusterfs",
        "lustre",
        "gpfs",
        "9p",
    }
)

VIRTUAL_FSTYPES = frozenset(
    {
        "proc",
        "sysfs",
        "tmpfs",
        "devtmpfs",
        "devpts",
        "cgroup",
        "cgroup2",
        "pstore",
        "securityfs",
        "debugfs",
        "tracefs",
        "configfs",
        "fusectl",
        "mqueue",
        "hugetlbfs",
        "ramfs",
        "autofs",
        "binfmt_misc",
        "nsfs",
        "bpf",
        "rpc_pipefs",
    }
)

CONTAINER_FSTYPES = frozenset({"overlay", "aufs", "squashfs"})
_IMAGE_SOURCE_RE = re.compile(r"\.(img|image|iso|qcow2|vdi|vmdk|raw)$", re.IGNORECASE)
_REMOTE_COLON_RE = re.compile(r"^[A-Za-z0-9_.-]+:/")


def read_mountinfo(path: str = "/proc/self/mountinfo") -> str:
    """Read mountinfo text.

    This is the sole live filesystem adapter.  Parsing and selection functions
    are pure and deterministic over text/entry inputs.
    """

    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _decode_mountinfo_field(value: str) -> str:
    """Decode Linux mountinfo octal escapes without interpreting other slashes."""

    out: List[str] = []
    i = 0
    while i < len(value):
        if (
            value[i] == "\\"
            and i + 3 < len(value)
            and all("0" <= c <= "7" for c in value[i + 1 : i + 4])
        ):
            out.append(chr(int(value[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def parse_mountinfo(text: str) -> List[MountEntry]:
    """Parse Linux ``/proc/self/mountinfo`` text into MountEntry records."""

    entries: List[MountEntry] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(" ")
        try:
            sep = parts.index("-")
        except ValueError as exc:
            raise ValueError(f"malformed mountinfo line {line_number}: missing separator") from exc
        if sep < 6 or len(parts) - sep - 1 < 3:
            raise ValueError(f"malformed mountinfo line {line_number}: too few fields")
        mount_id_s, parent_id_s, major_minor, root, mountpoint, options = parts[:6]
        optional = tuple(parts[6:sep])
        fstype = parts[sep + 1]
        source = parts[sep + 2]
        super_options = " ".join(parts[sep + 3 :])
        try:
            mount_id = int(mount_id_s)
            parent_id = int(parent_id_s)
        except ValueError as exc:
            raise ValueError(f"malformed mountinfo line {line_number}: invalid mount id") from exc
        if ":" not in major_minor:
            raise ValueError(f"malformed mountinfo line {line_number}: invalid major:minor")
        entries.append(
            MountEntry(
                mount_id=mount_id,
                parent_id=parent_id,
                major_minor=major_minor,
                root=_decode_mountinfo_field(root),
                mountpoint=_normalize_mountpoint(_decode_mountinfo_field(mountpoint)),
                options=_decode_mountinfo_field(options),
                optional_fields=tuple(_decode_mountinfo_field(field) for field in optional),
                fstype=_decode_mountinfo_field(fstype),
                source=_decode_mountinfo_field(source),
                super_options=_decode_mountinfo_field(super_options),
            )
        )
    return entries


def classify_mount(entry: MountEntry) -> MountDecision:
    """Classify one mount as selected, prohibited, or unsupported."""

    fstype = entry.fstype.lower()
    source = entry.source
    opts = _split_options(entry.options) | _split_options(entry.super_options)

    if is_boot_filesystem_path(entry.mountpoint):
        return MountDecision("prohibited", "boot-filesystem")
    if fstype in REMOTE_FSTYPES:
        return MountDecision("prohibited", "remote-fs")
    if fstype in VIRTUAL_FSTYPES:
        return MountDecision("prohibited", "virtual-fs")
    if fstype in CONTAINER_FSTYPES:
        return MountDecision("prohibited", "container-fs")
    if fstype == "fuse" or fstype.startswith("fuse."):
        return MountDecision("prohibited", "generic-fuse")
    if "_netdev" in opts:
        return MountDecision("prohibited", "netdev")
    if _is_remote_source(source):
        return MountDecision("prohibited", "remote-source")
    if _is_loop_or_image_source(source, entry.major_minor):
        return MountDecision("prohibited", "loop-source")
    if fstype in LOCAL_FSTYPES and entry.root != "/" and fstype != "btrfs":
        return MountDecision("prohibited", "bind-subtree")
    if fstype in LOCAL_FSTYPES:
        return MountDecision("selected", "local-fs")
    return MountDecision("unsupported", "unsupported-fstype")


def select_scan_roots(entries: Sequence[MountEntry], home_path: str = "/home") -> SelectionResult:
    """Select deterministic scan roots from parsed mount entries.

    Root filesystem entries are limited to ``home_path``. If ``home_path`` has
    its own eligible mount, that separate mount owns the scan exactly once and
    the root filesystem's synthetic home scan is skipped.
    """

    normalized_home = _normalize_mountpoint(home_path)
    exact_home_mount_exists = any(entry.mountpoint == normalized_home for entry in entries)
    selected_candidates: List[Tuple[MountEntry, str, str]] = []
    skipped: List[SkippedMount] = []

    for entry in entries:
        decision = classify_mount(entry)
        if decision.status != "selected":
            skipped.append(SkippedMount(entry.mount_id, entry.mountpoint, decision.reason, entry=entry))
            continue
        if entry.mountpoint == "/":
            selected_candidates.append((entry, normalized_home, "root-home"))
            skipped.append(SkippedMount(entry.mount_id, entry.mountpoint, "root-limited-to-home", entry=entry))
        else:
            selected_candidates.append((entry, entry.mountpoint, "selected"))

    if exact_home_mount_exists:
        kept: List[Tuple[MountEntry, str, str]] = []
        for entry, scan_root, reason in selected_candidates:
            if entry.mountpoint == "/" and scan_root == normalized_home:
                skipped.append(SkippedMount(entry.mount_id, entry.mountpoint, "root-home-covered", entry=entry))
            else:
                kept.append((entry, scan_root, reason))
        selected_candidates = kept

    groups = {}
    for entry, scan_root, reason in selected_candidates:
        groups.setdefault(_identity(entry), []).append((entry, scan_root, reason))

    selected: List[SelectedRoot] = []
    for group in groups.values():
        root_home_candidates = [item for item in group if item[2] == "root-home"]
        if root_home_candidates:
            chosen = min(root_home_candidates, key=lambda item: _choice_key(item[0], item[1]))
        else:
            chosen = min(group, key=lambda item: _choice_key(item[0], item[1]))
        chosen_entry, chosen_root, chosen_reason = chosen
        selected.append(
            SelectedRoot(
                mountpoint=chosen_root,
                entry=chosen_entry,
                source_mount_id=chosen_entry.mount_id,
                source_mountpoint=chosen_entry.mountpoint,
                reason=chosen_reason,
            )
        )
        for entry, scan_root, _reason in sorted(group, key=lambda item: _choice_key(item[0], item[1])):
            if entry is chosen_entry and scan_root == chosen_root:
                continue
            skipped.append(
                SkippedMount(
                    mount_id=entry.mount_id,
                    mountpoint=scan_root,
                    reason="duplicate",
                    chosen_mount_id=chosen_entry.mount_id,
                    entry=entry,
                )
            )

    selected.sort(key=lambda record: (record.entry.mount_id, len(_normalize_mountpoint(record.mountpoint)), _normalize_mountpoint(record.mountpoint)))
    skipped.sort(key=lambda record: (record.reason != "duplicate", record.mount_id, len(_normalize_mountpoint(record.mountpoint)), _normalize_mountpoint(record.mountpoint)))
    return SelectionResult(selected=selected, skipped=skipped)


def _split_options(options: str) -> set:
    return {part for part in options.split(",") if part}


def _is_remote_source(source: str) -> bool:
    return source.startswith("//") or bool(_REMOTE_COLON_RE.match(source))


def _is_loop_or_image_source(source: str, major_minor: str) -> bool:
    if source.startswith("/dev/loop"):
        return True
    major = major_minor.split(":", 1)[0]
    if major == "7":
        return True
    return bool(_IMAGE_SOURCE_RE.search(source))


def is_boot_filesystem_path(path: str) -> bool:
    normalized = _normalize_mountpoint(path)
    return normalized == "/boot" or normalized.startswith("/boot/")


def _identity(entry: MountEntry) -> Tuple[str, str, str, str]:
    return (entry.major_minor, entry.fstype.lower(), entry.root, entry.source)


def _normalize_mountpoint(path: str) -> str:
    if not path:
        return "/"
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path or "/"


def _choice_key(entry: MountEntry, scan_root: str) -> Tuple[int, int, str]:
    normalized = _normalize_mountpoint(scan_root)
    return (entry.mount_id, len(normalized), normalized)
