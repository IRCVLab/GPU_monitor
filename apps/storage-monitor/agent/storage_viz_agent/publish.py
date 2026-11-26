import gzip
import hashlib
import json
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
    if len(payload) > 65536:
        raise ValueError("manifest exceeds 64 KiB")
    atomic(data / "manifest.json", payload)


def prune(data, manifest, now, previous):
    # SHIM(M5b..M5c) S8: limit collection to v2 capture and staging files.
    current = {v["volume_id"]: v["capture"]["sha256"] for v in manifest["volumes"] if v["capture"]}
    for directory in (data / "captures").glob("*"):
        if not directory.is_dir():
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
