"""Recent completed scan durations per server, kept beside the central store.

Each server has its own file, written only by that server's telemetry worker.
The file sits outside the validated state record, so older collectors ignore it.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
from typing import Any

PLANS = ("full", "ssd")
LIMIT = 5
_SERVER_ID_RE = re.compile(r"[A-Za-z0-9_.-]+")


def path_for(root: pathlib.Path, server_id: str) -> pathlib.Path:
    if not _SERVER_ID_RE.fullmatch(server_id):
        raise ValueError("unsafe server id")
    return pathlib.Path(root) / "scan-duration-history" / f"{server_id}.json"


def parse(value: Any) -> dict[str, list[list[int]]]:
    if not isinstance(value, dict) or not set(value) <= set(PLANS):
        return {}
    result = {}
    for plan, entries in value.items():
        if not isinstance(entries, list) or len(entries) > LIMIT:
            return {}
        previous = 0
        for entry in entries:
            if not (isinstance(entry, list) and len(entry) == 2 and all(type(x) is int for x in entry)
                    and entry[0] > previous and entry[1] >= 0):
                return {}
            previous = entry[0]
        result[plan] = [list(entry) for entry in entries]
    return result


def load(path: pathlib.Path) -> dict[str, list[list[int]]]:
    try:
        if path.parent.is_symlink():
            return {}
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as handle:
            return parse(json.load(handle))
    except (OSError, ValueError):
        return {}


def save(path: pathlib.Path, history: dict[str, list[list[int]]]) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if path.parent.is_symlink():
        raise OSError("scan duration history directory must not be a symlink")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(history, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
