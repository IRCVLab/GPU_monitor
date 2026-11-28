import gzip
import hashlib
import json
import logging
import shutil

from .ids import VOLUME_ID_RE
import os
from pathlib import Path
import tempfile


def encode(obj):
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).encode()


def sync_dir(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as tmp:
        try:
            os.fchmod(tmp.fileno(), 0o640)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(tmp.name, path)
            sync_dir(path.parent)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def store_capture(data, capture):
    directory = data / "captures" / capture["volume_id"]
    directory.mkdir(parents=True, exist_ok=True)
    sync_dir(data)
    sync_dir(directory.parent)
    payload = gzip.compress(encode(capture), mtime=0)
    sha = hashlib.sha256(payload).hexdigest()
    path = directory / (sha + ".json.gz")
    if not path.exists():
        atomic(path, payload)
    return dict(sha256=sha, bytes=len(payload), **{k: capture[k] for k in
                ("scan_started_unix", "scan_finished_unix", "outcome")})


def commit_manifest(data, manifest, now):
    manifest.update(seq=manifest["seq"] + 1, written_unix=now)
    payload = encode(manifest)
    limit = 64 * 1024
    if len(payload) > limit:
        # A partial list could authorize removal across an omitted mount.
        manifest["mount_table"].update(status="unreadable", within_roots=[])
        payload = encode(manifest)
    if len(payload) > limit:
        # Keep receipts and capture references ahead of optional telemetry.
        wire = dict(manifest, volumes=[dict(v, capacity=None) for v in manifest["volumes"]])
        payload = encode(wire)
        if len(payload) > limit:
            for volume in wire["volumes"]:
                volume["last_attempt"] = None
            payload = encode(wire)
        if len(payload) > limit:
            # Even the required root inventory can exceed the entire budget.
            # Publish only whole volume records that fit and flag incomplete coverage.
            candidates, wire["volumes"] = wire["volumes"], []
            if wire["last_run"] and wire["last_run"]["outcome"] in ("complete", "nothing_due"):
                wire["last_run"] = dict(wire["last_run"], outcome="partial")
            size = len(encode(wire))
            for volume in candidates:
                extra = len(encode(volume)) + bool(wire["volumes"])
                if size + extra <= limit:
                    wire["volumes"].append(volume)
                    size += extra
            payload = encode(wire)
        logging.warning("Degraded oversized host manifest to %d bytes", len(payload))
    atomic(data / "manifest.json", payload)


def _remove_entry(parent, name):
    """Unlink relative to an open directory, opening child directories without following links."""
    try:
        os.unlink(name, dir_fd=parent)
    except IsADirectoryError:
        child = os.open(name, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for entry in os.listdir(child):
                _remove_entry(child, entry)
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=parent)
    except FileNotFoundError:
        pass


def prune(data, manifest, now, previous):
    parent = os.open(data, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _remove_entry(parent, "snapshots")
        for name in ("scan-status.json", "scan-lifecycle.json", "slow-capture.json.gz"):
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass
    finally:
        os.close(parent)
    current = {v["volume_id"]: v["capture"]["sha256"] for v in manifest["volumes"] if v["capture"]}
    present = {v["volume_id"] for v in manifest["volumes"]}
    for directory in (data / "captures").glob("*"):
        if directory.is_symlink() or not VOLUME_ID_RE.fullmatch(directory.name) or not directory.is_dir():
            continue
        if directory.name not in present:
            shutil.rmtree(directory)
            continue
        files = sorted(directory.glob("*.json.gz"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        others = [p for p in files if p.name.split(".")[0] != current.get(directory.name)]
        prior = previous.get(directory.name)
        if prior and prior != current.get(directory.name):
            keep = prior + ".json.gz"
        else:
            keep = others[0].name if others else None
        for path in others:
            if path.name != keep:
                path.unlink()
    for pattern in ("*.tmp", "captures/*/*.tmp"):
        for path in data.glob(pattern):
            if path.stat().st_mtime < now - 3600:
                path.unlink()
