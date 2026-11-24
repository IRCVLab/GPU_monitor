"""Collect bounded, structured experiment evidence over the monitoring SSH account."""
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ssh_client import SSHClient

if TYPE_CHECKING:
    from ..models import Server


_ENTRYPOINT_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}"
_CONFIG_SUFFIXES = (".yaml", ".yml", ".json", ".toml", ".py")
_MAX_PROCESSES = 32
_MAX_GPU_INDEX = 255
_MAX_ETA_SECONDS = 14 * 86400


_PROBE_SOURCE = (
    f"MAX_PROCESSES = {_MAX_PROCESSES}\n"
    f"MAX_GPU_INDEX = {_MAX_GPU_INDEX}\n"
    f"MAX_ETA_SECONDS = {_MAX_ETA_SECONDS}\n"
    f"ENTRYPOINT_PATTERN = {_ENTRYPOINT_PATTERN!r}\n"
    f"CONFIG_SUFFIXES = {_CONFIG_SUFFIXES!r}\n"
) + r"""
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from contextlib import ExitStack
from itertools import islice
from pathlib import Path

LOG_SUFFIXES = {".log", ".out", ".txt", ".err"}
LOG_MAX_AGE_SECONDS = 300
LOG_TAIL_BYTES = 8192
MAX_EXTRA_FDS = 30
MAX_LOG_TAILS = 4
MAX_NVIDIA_OUTPUT = 65536


def query_nvidia(fields, source):
    output = subprocess.run(
        ["nvidia-smi", f"--query-{source}={fields}", "--format=csv,noheader,nounits"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2,
    ).stdout
    if len(output) > MAX_NVIDIA_OUTPUT:
        raise ValueError("Unexpected GPU inventory size")
    return output.decode("ascii").splitlines()


def gpu_processes(gpus, applications):
    indices = {}
    for line in gpus:
        gpu_uuid, _, index = line.partition(",")
        index = index.strip()
        if index.isdecimal() and len(index) <= 3 and int(index) <= MAX_GPU_INDEX:
            indices[gpu_uuid.strip()] = int(index)
    jobs = {}
    for line in applications:
        gpu_uuid, _, pid = line.partition(",")
        pid = pid.strip()
        index = indices.get(gpu_uuid.strip())
        if index is None or not pid.isdecimal() or len(pid) > 10 or int(pid) == 0:
            continue
        jobs.setdefault(int(pid), set()).add(index)
    return dict(sorted(jobs.items())[:MAX_PROCESSES])


def read_bounded(path, limit):
    with path.open("rb", buffering=0) as stream:
        return stream.read(limit)


def task_identity(process):
    tail = read_bounded(process / "stat", 4096).rpartition(b")")[2].split()
    return int(tail[19]), int(tail[2])


def config_basename(candidate):
    name = os.path.basename(candidate)
    if name.lower().endswith(CONFIG_SUFFIXES) and re.fullmatch(ENTRYPOINT_PATTERN, name):
        return name
    return None


def config_name(program, args):
    if args and program in {"train.py", "train_net.py", "test.py"}:
        positional = config_basename(args[0]) if not args[0].startswith("-") else None
    else:
        positional = None
    for index, argument in enumerate(args):
        flag, separator, value = argument.partition("=")
        if flag == "--":
            break
        if flag not in {"--config", "--config-file"}:
            continue
        if not separator:
            value = args[index + 1] if index + 1 < len(args) else ""
        if not value.startswith("-"):
            return config_basename(value)
    return positional


def command_context(process):
    command = read_bounded(process / "cmdline", 4096)
    if not command:
        return None
    # The last token may be incomplete when cmdline exceeds the read bound.
    argv = [os.fsdecode(token) for token in command.split(b"\0")[:-1]]
    if not argv:
        return None
    program = os.path.basename(argv[0])
    args = argv[1:]
    if re.fullmatch(r"python(?:[23](?:\.\d+)?)?", program):
        options = {"-u", "-B", "-O", "-OO", "-E", "-s", "-S", "-I", "-P", "-q"}
        index = 1
        while index < len(argv) and argv[index] in options:
            index += 1
        args = []
        if index < len(argv):
            argument = argv[index]
            if argument == "-m" and index + 1 < len(argv):
                module = argv[index + 1]
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module):
                    program = module
                    args = argv[index + 2:]
            elif not argument.startswith("-") and argument.endswith(".py"):
                program = os.path.basename(argument)
                args = argv[index + 1:]
    else:
        try:
            program = os.path.basename(os.readlink(process / "exe"))
        except OSError:
            pass
    if re.fullmatch(ENTRYPOINT_PATTERN, program):
        return program, config_name(program, args)
    return None


def parse_eta(line):
    clock = r"(\d{1,4}:\d{2}(?::\d{2})?)(?![\d:])"
    eta = re.search(r"\b(?:overall|total|run)\s+eta\s*[:=]\s*" + clock, line, re.IGNORECASE)
    if eta is None and not re.search(r"\b(?:epoch|step|batch|validation|evaluation)\s+eta\s*[:=]", line, re.IGNORECASE):
        eta = re.search(r"\beta\s*[:=]\s*" + clock, line, re.IGNORECASE)
    if eta is None and re.search(r"\b(?:overall|total|run)\s+\d{1,3}%\s*\|", line, re.IGNORECASE):
        eta = re.search(r"\[[\d:]+<" + clock, line)
    if eta:
        parts = [int(part) for part in eta[1].split(":")]
        if all(part < 60 for part in parts[1:]):
            duration = sum(part * 60 ** index for index, part in enumerate(reversed(parts)))
            if 0 < duration <= MAX_ETA_SECONDS:
                return duration
    return None


def writable_descriptor(process, descriptor, inode):
    raw = read_bounded(process / "fdinfo" / str(descriptor), 512)
    fields = dict(line.split(b":", 1) for line in raw.splitlines() if b":" in line)
    try:
        flags = int(fields[b"flags"], 8)
        if b"ino" in fields and int(fields[b"ino"]) != inode:
            return False
    except (KeyError, ValueError):
        return False
    return (flags & os.O_ACCMODE) in (os.O_WRONLY, os.O_RDWR)


def open_log(process, descriptor, cwd, uid, pins):
    # O_PATH pins the target without opening a possible pipe or device for reading.
    pinned = os.open(process / "fd" / str(descriptor), os.O_PATH)
    pins.callback(os.close, pinned)
    info = os.fstat(pinned)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid:
        return None
    pinned_link = Path("/proc/self/fd") / str(pinned)
    target = Path(os.readlink(pinned_link))
    if target.suffix.lower() not in LOG_SUFFIXES or cwd not in target.parents:
        return None
    if not writable_descriptor(process, descriptor, info.st_ino):
        return None
    return pinned_link, info


def read_log(pinned_link, info, now):
    age = now - info.st_mtime
    if age > LOG_MAX_AGE_SECONDS or age < -1:
        return None
    with pinned_link.open("rb", buffering=0) as stream:
        offset = max(0, info.st_size - LOG_TAIL_BYTES)
        stream.seek(offset)
        lines = stream.read(LOG_TAIL_BYTES).decode("utf-8", errors="replace").splitlines()
    if offset:
        lines = lines[1:]
    line = next((line for line in reversed(lines) if line.strip()), "")
    eta = parse_eta(line)
    remaining = eta - max(0, int(age)) if eta else None
    if remaining and remaining > 0:
        return info.st_mtime, remaining
    return None


def log_descriptors(process):
    extras = []
    try:
        with os.scandir(process / "fd") as entries:
            for entry in islice(entries, MAX_EXTRA_FDS + 3):
                if entry.name.isdecimal() and int(entry.name) > 2:
                    extras.append(int(entry.name))
    except OSError:
        pass
    return [1, 2, *sorted(extras)[:MAX_EXTRA_FDS]]


def log_evidence(process, cwd, uid, now):
    logs = {}
    with ExitStack() as pins:
        for fd in log_descriptors(process):
            try:
                opened = open_log(process, fd, cwd, uid, pins)
                if opened is not None:
                    path, info = opened
                    logs.setdefault((info.st_dev, info.st_ino), (path, info))
            except OSError:
                continue
        # Descriptor order is stable for grouping even as newer output is written.
        group_log = next(iter(logs), None)
        recent = sorted(logs.values(), key=lambda log: log[1].st_mtime, reverse=True)
        for path, info in recent[:MAX_LOG_TAILS]:
            try:
                evidence = read_log(path, info, now)
                if evidence is not None:
                    return group_log, evidence
            except OSError:
                continue
        return group_log, None


def run_identity(proc, boot, pid, start, pgid, command, uid, group_log):
    key = ("process", boot, pid, start)
    if group_log is not None:
        leader = proc / str(pgid)
        try:
            leader_start, leader_group = task_identity(leader)
            if leader_group == pgid and leader_start <= start and leader.stat().st_uid == uid:
                device, inode = group_log
                key = ("group", boot, pgid, leader_start, command, device, inode)
        except (OSError, IndexError, ValueError):
            pass
    return hashlib.sha256(repr(key).encode()).hexdigest()[:32]


def snapshot(proc, boot, jobs, maximum_start=None):
    rows = {}
    evidence_times = {}
    now = time.time()
    for pid, indices in jobs.items():
        process = proc / str(pid)
        try:
            initial = task_identity(process)
            start, pgid = initial
            if maximum_start is not None and start > maximum_start:
                continue
            uid = process.stat().st_uid
            command = command_context(process)
            if command is None:
                continue
            program, config = command
            group_log, evidence = None, None
            try:
                cwd = Path(os.readlink(process / "cwd"))
                if cwd.is_absolute():
                    group_log, evidence = log_evidence(process, cwd, uid, now)
            except OSError:
                pass
            if task_identity(process) != initial:
                continue
            identifier = run_identity(proc, boot, pid, start, pgid, command, uid, group_log)
        except (OSError, IndexError, ValueError):
            continue
        row = rows.setdefault(identifier, {
            "id": identifier, "entrypoint": program, "gpu_indices": set(),
            "eta_seconds": None, "config_name": config,
        })
        row["gpu_indices"].update(indices)
        if evidence is not None and evidence[0] > evidence_times.get(identifier, 0):
            evidence_times[identifier], row["eta_seconds"] = evidence
    return [{**row, "gpu_indices": sorted(row["gpu_indices"])} for row in rows.values()]


if __name__ == "__main__":
    proc = Path("/proc")
    boot = read_bounded(proc / "sys/kernel/random/boot_id", 64).decode("ascii").strip()
    maximum_start = int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf("SC_CLK_TCK")) - 1
    jobs = gpu_processes(query_nvidia("uuid,index", "gpu"), query_nvidia("gpu_uuid,pid", "compute-apps"))
    print(json.dumps(snapshot(proc, boot, jobs, maximum_start)))
"""
_PROBE_COMMAND = "python3 - <<'GPU_EXPERIMENT_PROBE'\n" + _PROBE_SOURCE + "\nGPU_EXPERIMENT_PROBE"


@dataclass(frozen=True)
class ExperimentEvidence:
    id: str
    entrypoint: str
    gpu_indices: tuple[int, ...]
    eta_seconds: int | None
    config_name: str | None = None


def _decode_evidence(raw: str) -> list[ExperimentEvidence]:
    error = "Invalid experiment evidence"
    if len(raw) > 32768:
        raise ValueError(error)
    try:
        rows = json.loads(raw)
    except ValueError:
        raise ValueError(error) from None
    if not isinstance(rows, list) or len(rows) > _MAX_PROCESSES:
        raise ValueError(error)
    seen = set()
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {
            "id", "entrypoint", "gpu_indices", "eta_seconds", "config_name",
        }:
            raise ValueError(error)
        identifier, entrypoint, indices, eta, config = (
            row["id"], row["entrypoint"], row["gpu_indices"], row["eta_seconds"], row["config_name"],
        )
        if (
            not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier)
            or identifier in seen
            or not isinstance(entrypoint, str) or not re.fullmatch(_ENTRYPOINT_PATTERN, entrypoint)
            or not isinstance(indices, list) or not 1 <= len(indices) <= _MAX_GPU_INDEX + 1
            or any(type(index) is not int or not 0 <= index <= _MAX_GPU_INDEX for index in indices)
            or sorted(set(indices)) != indices
            or (eta is not None and (type(eta) is not int or not 1 <= eta <= _MAX_ETA_SECONDS))
            or (config is not None and (
                not isinstance(config, str) or not re.fullmatch(_ENTRYPOINT_PATTERN, config)
                or not config.lower().endswith(_CONFIG_SUFFIXES)
            ))
        ):
            raise ValueError(error)
        seen.add(identifier)
        result.append(ExperimentEvidence(identifier, entrypoint, tuple(indices), eta, config))
    return result


def collect_experiments(server: "Server") -> list[ExperimentEvidence]:
    ssh = SSHClient(server)
    try:
        ssh.connect(handshake_timeout=4)
        return _decode_evidence(ssh.run_bounded(_PROBE_COMMAND, timeout=8))
    finally:
        ssh.close()
