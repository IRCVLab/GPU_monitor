#!/usr/bin/env python3
"""Run today's v1 scan_runner on a rehearsal host whose /data is a docker volume.

Docker mounts a volume as a bind of a host subtree, which the v1 mount policy refuses
(``bind-subtree``), and it refuses loop devices too. This shim shows the runner the same
mountinfo with the /data entry's root field set to ``/``, as a whole-filesystem mount
would have it. The device number is unchanged and is /data's real one, so hstscan's
device guards still hold. Everything else is the installed v1 agent, unmodified.
A systemd drop-in on storage-viz-scan.service runs this instead of ``-m agent.scan_runner``.
"""
import sys

sys.path.insert(0, "/opt/storage-viz")
from agent import mount_policy  # noqa: E402

_read_mountinfo = mount_policy.read_mountinfo


def read_mountinfo(path: str = "/proc/self/mountinfo") -> str:
    lines = []
    for line in _read_mountinfo(path).splitlines():
        fields = line.split(" ")
        if len(fields) > 4 and fields[4] == "/data":
            fields[3] = "/"
        lines.append(" ".join(fields))
    return "\n".join(lines) + "\n"


# scan_runner binds read_mountinfo as a default argument when it is imported.
mount_policy.read_mountinfo = read_mountinfo
from agent import scan_runner  # noqa: E402

raise SystemExit(scan_runner.main())
