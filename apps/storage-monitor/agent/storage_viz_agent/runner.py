"""The host's single-writer scan transaction."""
import errno
import fcntl
import os
from itertools import islice
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import time
import uuid

from . import capacity, capture, identity, mount_policy, plan, publish
from .block_media import BlockMediaResolver
from .config import load_config
from .ids import REQUEST_ID_RE

STATES = ("idle", "recovering", "preparing", "scanning")
EVENTS = ("busy", "stale", "start", "recovered", "empty", "planned", "group_done", "queued", "finish")
IGNORE = None
# Columns follow EVENTS. IGNORE is explicit even for redundant notifications.
_ROWS = {
    "idle": ("idle", "recovering", "preparing", IGNORE, IGNORE, IGNORE, IGNORE, IGNORE, IGNORE),
    "recovering": (IGNORE, IGNORE, IGNORE, "preparing", IGNORE, IGNORE, IGNORE, IGNORE, IGNORE),
    "preparing": (IGNORE, IGNORE, IGNORE, IGNORE, "idle", "scanning", IGNORE, IGNORE, IGNORE),
    "scanning": (IGNORE, IGNORE, IGNORE, IGNORE, IGNORE, IGNORE, "scanning", "preparing", "idle"),
}
TRANSITIONS = {(state, event): target for state, row in _ROWS.items()
               for event, target in zip(EVENTS, row)}


def transition(state, event):
    return TRANSITIONS[state, event] or state


def requests(data):
    directory = data / "requests"
    if not directory.exists():
        return {}
    # Bootstrap provisions the root-owned directory setgid to the collector group.
    owner_group = directory.stat().st_gid
    valid = {}
    for path in sorted(directory.iterdir()):
        if not REQUEST_ID_RE.fullmatch(path.name):
            continue
        try:
            fd = os.open(path, os.O_NOFOLLOW | os.O_NONBLOCK | os.O_RDONLY)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOENT, errno.EACCES):
                continue
            raise
        try:
            st = os.fstat(fd)
            if (stat.S_ISREG(st.st_mode) and st.st_gid == owner_group and st.st_size <= 64
                    and os.read(fd, 65) in (path.name.encode(), path.name.encode() + b"\n")):
                valid[path.name] = int(st.st_mtime)
        finally:
            os.close(fd)
    return valid


def discover(cfg, reader, resolver):
    entries = mount_policy.parse_mountinfo(reader())
    selection = mount_policy.select_scan_roots(entries, root_directory_paths=
                                              mount_policy.verified_root_directory_paths(entries))
    roots = identity.resolve(selection.selected, cfg.volume_aliases, cfg.config_digest,
                             resolver, devices=[e.major_minor for e in entries])
    return roots, mount_policy.mounts_within_roots(entries, roots)


def prepare_volumes(manifest, roots, now):
    old = {v["volume_id"]: v for v in manifest["volumes"]}
    volumes = {}
    for group, members in plan.validate_partition(roots).items():
        for vid, rs in members.items():
            r = rs[0]
            previous = old.get(vid, {})
            descriptors = [m.descriptor() for m in rs]
            reuse = r.identity != "ambiguous" and descriptors == previous.get("roots")
            volumes[vid] = dict(volume_id=vid, identity=r.identity,
                diagnostic="VOLUME_IDENTITY_AMBIGUOUS" if r.identity == "ambiguous" else None,
                mountpoint=r.mountpoint, fstype=r.fstype, media=r.media, media_confidence=r.media_confidence,
                group=group, cadence_sec=plan.CADENCE.get(group, plan.CADENCE["slow"]),
                roots=descriptors, capacity=capacity.sample(r, previous.get("capacity"), now),
                capture=previous.get("capture") if reuse else None,
                last_attempt=previous.get("last_attempt") if reuse else None)
    manifest["volumes"] = list(volumes.values())
    return volumes


def run_group(cfg, record, group, volumes, execute, clock):
    start = record["current"]["started_unix"]
    # Raw scanner output can exceed /run tmpfs capacity. Keep it on the
    # capture filesystem, where abandoned staging files are collected.
    with tempfile.NamedTemporaryFile(dir=cfg.data_dir, prefix="scan-", suffix=".tmp") as tmp:
        out = Path(tmp.name)
        argv = [cfg.scanner_path, "--threads", str(cfg.threads), "--prune-home", str(cfg.prune_home_mb),
                "--prune-data", str(cfg.prune_data_mb), "--top", str(cfg.top), "--stale-days",
                str(cfg.stale_days), "--run-id", record["run_id"], "--group", group,
                "--progress", str(cfg.run_dir / "progress.json"), "--out", str(out)]
        for members in volumes.values():
            for root in members:
                argv.extend(["--target", root.path, root.device, root.prune_class])
        try:
            if execute(argv).returncode == 0:
                return capture.split_group_output(capture.read(out), volumes, record["run_id"],
                                                  group, start, clock())
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            pass
    return {}


def finish(manifest, record, outcome, now, received):
    rid = record["run_id"]
    manifest["last_run"] = dict(run_id=rid, trigger=record["trigger"],
        plan="full" if any(g["group"] != "fast" for g in record["plan"]) else "ssd",
        started_unix=record["started_unix"], finished_unix=now, outcome=outcome,
        agent_sha=manifest["agent"]["release_sha"])
    receipts = {r["request_id"]: r for r in manifest["requests"]}
    for request in record["request_ids"]:
        if request not in receipts:
            receipts[request] = dict(request_id=request, run_id=rid,
                received_unix=received.get(request, record["started_unix"]), outcome=outcome)
    manifest["requests"] = list(receipts.values())[-16:]


def aggregate(outcomes):
    if not outcomes or all(o == "failed" for o in outcomes):
        return "failed"
    return "complete" if all(o == "complete" for o in outcomes) else "partial"


def run_once(config_path, scheduled=False, release=None, mountinfo_reader=mount_policy.read_mountinfo,
             media_resolver=None, scanner_runner=subprocess.run, clock=lambda: int(time.time()),
             persisted=lambda step: None):
    cfg = load_config(config_path)
    if cfg.run_dir.is_symlink() or cfg.data_dir.is_symlink():
        raise ValueError("managed directory is a symlink")
    cfg.run_dir.mkdir(exist_ok=True, parents=True)
    result = 75
    while True:
        with (cfg.run_dir / "scan.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return result
            result = _locked(cfg, scheduled, release or {}, mountinfo_reader,
                             media_resolver or BlockMediaResolver(), scanner_runner, clock, persisted)
        # Close the enqueue/unlock race: the gate may have seen our lock
        # after our final inbox check. An enqueue after unlock starts a unit.
        if not requests(cfg.data_dir):
            return result
        scheduled = True


def _locked(cfg, scheduled, release, reader, resolver, execute, clock, persisted):
    data = cfg.data_dir
    data.mkdir(parents=True, exist_ok=True)
    journal = data / "run.json"
    stale = capture.read(journal)
    state = transition("idle", "stale" if stale else "start")
    record = dict(schema="storage-viz.run/2", run_id=os.environ.get("INVOCATION_ID") or uuid.uuid4().hex,
        trigger="timer" if scheduled else "manual", started_unix=clock(), request_ids=[], plan=[],
        current=None, done_volumes=[])

    def save():
        publish.atomic(journal, publish.encode(record))
        persisted("record")

    def commit():
        publish.commit_manifest(data, manifest, clock())
        persisted("manifest")

    def clear():
        journal.unlink()
        publish.sync_dir(data)
        persisted("clear")

    if not stale:
        save()
    manifest = capture.read(data / "manifest.json", dict(schema="storage-viz.manifest/2", seq=0,
        server_id=cfg.server_id, hostname=socket.gethostname().split(".")[0], volumes=[], requests=[],
        mount_table=dict(status="unreadable", observed_unix=0, within_roots=[]), last_run=None))
    manifest.update(agent=dict(release_sha=release.get("source_sha", "0"*40),
        release_seq=release.get("release_seq", 0), config_digest=cfg.config_digest),
        hostname=socket.gethostname().split(".")[0], server_id=cfg.server_id)
    if state == "recovering":
        last = manifest.get("last_run") or {}
        final = last.get("run_id") == stale["run_id"] and last["finished_unix"] >= stale["started_unix"]
        recorded = {r["request_id"] for r in manifest["requests"]}
        if not final or set(stale["request_ids"]) - recorded:
            unfinished = {vid for g in stale["plan"] for vid in g["volumes"]} - set(stale["done_volumes"])
            for v in manifest["volumes"]:
                # The group commit can reach disk just before done_volumes.
                attempt = v.get("last_attempt") or {}
                if v["volume_id"] in unfinished and (final or attempt.get("run_id") != stale["run_id"]):
                    v["last_attempt"] = dict(run_id=stale["run_id"], started_unix=stale["started_unix"],
                        finished_unix=clock(), outcome="interrupted", error_code="INTERRUPTED")
            finish(manifest, stale, "interrupted", clock(), {})
            commit()
        clear()
        state = transition(state, "recovered")
        save()

    try:
        roots, within = discover(cfg, reader, resolver)
    except (ValueError, OSError):
        # Preserve captures when discovery cannot establish the current roots.
        roots = []
        manifest["mount_table"].update(status="unreadable")
        volumes = {v["volume_id"]: v for v in manifest["volumes"]}
        for volume in volumes.values():
            volume["capacity"]["status"] = "failed"
    else:
        manifest["mount_table"] = dict(status="ok", observed_unix=clock(), within_roots=within)
        volumes = prepare_volumes(manifest, roots, clock())
    root_by_vid = {}
    for root in sorted(roots, key=lambda r: r.path):
        root_by_vid.setdefault(root.volume_id, root)
    samples = {vid: v["capacity"] for vid, v in volumes.items()}
    while state == "preparing":
        known = {r["request_id"] for r in manifest["requests"]}
        pending = requests(data)
        received = dict(islice(((k, t) for k, t in pending.items() if k not in known), 16))
        previous = {vid: v["capture"]["sha256"] for vid, v in volumes.items() if v["capture"]}
        groups = plan.due_groups(manifest, roots, clock(), record["trigger"], received)
        record.update(request_ids=list(received), plan=[dict(group=g, volumes=list(vs)) for g, vs in groups.items()],
                      current=None, done_volumes=[])
        # Commit inventory before journaling a plan so recovery can identify
        # even newly discovered volumes. Every group then owns only its rows.
        if groups:
            commit()
        save()
        for key in set(received) | (pending.keys() & known):
            (data / "requests" / key).unlink(missing_ok=True)
        if pending:
            publish.sync_dir(data / "requests")
            persisted("consume")
        state = transition(state, "planned" if groups else "empty")
        outcomes = []
        for group, members in groups.items():
            record["current"] = dict(group=group, started_unix=clock())
            save()
            captures = run_group(cfg, record, group, members, execute, clock)
            samples = {vid: capacity.sample(root, samples[vid], clock()) for vid, root in root_by_vid.items()}
            for vid in members:
                v = volumes[vid]
                c = captures.get(vid)
                outcome = c["outcome"] if c else "failed"
                if c:
                    v["capture"] = publish.store_capture(data, c)
                    persisted("capture")
                v["last_attempt"] = dict(run_id=record["run_id"], started_unix=record["current"]["started_unix"],
                    finished_unix=clock(), outcome=outcome, error_code=None if c else "SCANNER_FAILED")
                v["capacity"] = samples[vid]
                outcomes.append(outcome)
            commit()
            record["done_volumes"].extend(members)
            save()
            state = transition(state, "group_done")
        outcome = aggregate(outcomes) if groups or not roots else "nothing_due"
        for vid, v in volumes.items():
            v["capacity"] = samples[vid]
        finish(manifest, record, outcome, clock(), received)
        commit()
        publish.prune(data, manifest, clock(), previous)
        new = requests(data).keys() - {r["request_id"] for r in manifest["requests"]}
        if state == "scanning":
            state = transition(state, "queued" if new else "finish")
        elif new:
            state = transition(state, "start")
        if state == "preparing":
            # All passes in the unit share the systemd invocation identity.
            record.update(trigger="manual",
                          request_ids=[], plan=[], current=None, done_volumes=[])
            save()
    clear()
    return 1 if outcome == "failed" else 0
