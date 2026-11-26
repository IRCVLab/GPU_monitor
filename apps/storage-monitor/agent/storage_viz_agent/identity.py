"""Resolve filesystem identities independently of namespace-local mount IDs."""
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Optional


def display_path(path):
    # mountinfo uses surrogateescape. Match hstscan's one U+FFFD per invalid byte.
    return "".join("\ufffd" if 0xdc80 <= ord(ch) <= 0xdcff else ch for ch in path)


def digest(prefix, *parts):
    return prefix + hashlib.sha256(b"\0".join(os.fsencode(p) for p in parts)).hexdigest()[:16]


@dataclass(frozen=True)
class Root:
    path: str
    mountpoint: str
    device: str
    volume_id: str
    identity: str
    binding: str
    prune_class: str
    fstype: str
    media: str
    media_confidence: str

    diagnostic: Optional[str] = None

    def descriptor(self):
        return dict(scan_root=display_path(self.path), binding=self.binding, prune_class=self.prune_class)


def device_uuid(device, sysfs, devfs, udev):
    try:
        node = (sysfs / "dev/block" / device).resolve(strict=True)
    except OSError:
        return None
    # by-uuid has just one symlink for cloned filesystems. udev retains the
    # probed ID_FS_UUID for *each* device, including the losing link claimant.
    try:
        for line in (udev / ("b" + device)).read_text().splitlines():
            if line.startswith("E:ID_FS_UUID="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    try:
        for link in (devfs / "disk/by-uuid").iterdir():
            if link.resolve().name == node.name:
                return link.name
    except OSError:
        pass
    return None


def resolve(mounts, aliases, config_digest, media_resolver, sysfs=Path("/sys"),
            devfs=Path("/dev"), udev=Path("/run/udev/data"), boot_id=None, devices=()):
    boot_id = boot_id or Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    uuids = {dev: device_uuid(dev, sysfs, devfs, udev)
             for dev in set(devices) | {m.entry.major_minor for m in mounts}}
    owners = defaultdict(set)
    labels = defaultdict(set)
    for device, value in uuids.items():
        owners[value].add(device)
    for mount in mounts:
        for path in (mount.mountpoint, mount.entry.mountpoint):
            if path in aliases:
                labels[mount.entry.major_minor].add(aliases[path])
    alias_devices = defaultdict(set)
    for device, names in labels.items():
        for name in names:
            alias_devices[name].add(device)
    resolved = []
    for m in mounts:
        path, entry = m.mountpoint, m.entry
        dev = entry.major_minor
        subpath = os.path.normpath(os.path.join(entry.root, os.path.relpath(path, entry.mountpoint)))
        diagnostic = None
        if len(labels[dev]) > 1 or any(len(alias_devices[name]) > 1 for name in labels[dev]):
            diagnostic = "VOLUME_ALIAS_CONFLICT"
        if labels[dev] and not diagnostic:
            identity = "alias"
            vid = digest("v-", identity, next(iter(labels[dev])))
        elif not diagnostic and uuids[dev] and len(owners[uuids[dev]]) == 1:
            identity = "uuid"
            vid = digest("v-", identity, uuids[dev])
        else:
            diagnostic = diagnostic or "VOLUME_IDENTITY_AMBIGUOUS"
            identity = "ambiguous"
            vid = digest("x-", boot_id, dev, subpath)
        prune = "home" if path == "/home" else "data"
        binding = digest("b-", vid, subpath, prune, config_digest, "storage-viz.capture/2")
        media = media_resolver.resolve(dev)
        resolved.append(Root(path, entry.mountpoint, dev, vid, identity, binding, prune, entry.fstype,
                             media.media, media.confidence, diagnostic))
    return resolved
