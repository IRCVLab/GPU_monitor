import gzip
import hashlib
import json
import logging
from contextlib import contextmanager

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


@contextmanager
def _directory(name, parent=None):
    fd = os.open(name, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _remove_entry(parent, name):
    """Remove a directory tree without resolving symlinks in it."""
    try:
        os.unlink(name, dir_fd=parent)
    except IsADirectoryError:
        with _directory(name, parent) as child:
            for entry in os.listdir(child):
                _remove_entry(child, entry)
        os.rmdir(name, dir_fd=parent)
    except FileNotFoundError:
        pass


def _prune_temp(directory, now):
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.endswith(".tmp") and entry.is_file(follow_symlinks=False):
                if entry.stat(follow_symlinks=False).st_mtime < now - 3600:
                    os.unlink(entry.name, dir_fd=directory)


def _prune_captures(directory, current, prior):
    with os.scandir(directory) as entries:
        others = [(entry.stat(follow_symlinks=False).st_mtime_ns, entry.name) for entry in entries
                  if entry.name.endswith(".json.gz") and entry.name != current
                  and entry.is_file(follow_symlinks=False)]
    if not others:
        return
    keep = prior if prior and prior != current else max(others)[1]
    for _, name in others:
        if name != keep:
            os.unlink(name, dir_fd=directory)


def prune(data, manifest, now, previous):
    volumes = {volume["volume_id"]: volume["capture"] for volume in manifest["volumes"]}
    with _directory(data) as parent:
        _remove_entry(parent, "snapshots")
        for name in ("scan-status.json", "scan-lifecycle.json", "slow-capture.json.gz"):
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass
        _prune_temp(parent, now)
        try:
            with _directory("captures", parent) as captures:
                for vid in os.listdir(captures):
                    if not VOLUME_ID_RE.fullmatch(vid):
                        continue
                    if vid not in volumes:
                        _remove_entry(captures, vid)
                        continue
                    try:
                        with _directory(vid, captures) as directory:
                            current = volumes[vid]
                            prior = previous.get(vid)
                            _prune_captures(directory, current and current["sha256"] + ".json.gz",
                                            prior and prior + ".json.gz")
                            _prune_temp(directory, now)
                    except NotADirectoryError:
                        continue
        except (FileNotFoundError, NotADirectoryError):
            return
