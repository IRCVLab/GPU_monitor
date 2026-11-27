"""Legacy adapter for today's agents: the Python probe and the v1 telemetry it reads.

SHIM(M2b..M3b) S2: the gate's ``observe`` verb replaces the probe, and this module goes
with the SFTP code in M3b. Everything here is a parse boundary: ``parse_probe`` turns the
probe's bytes into an ``Observation`` that the actor trusts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
from typing import Any, Callable, Mapping, Optional

from collector import judge
from collector.inventory import Server
from collector.contract import AgentFacts, HostRun, ManifestPointer
from collector.ids import REQUEST_ID_RE, SHA256_RE as HEX64_RE, RUN_ID_RE
from collector.jobs import RescanReply, Scan
from collector.transport import KNOWN_ACTIVE_STATES, OpenSshTransport, TransportError, validate_status_envelope

MANUAL_UNIT = "storage-viz-scan.service"
SCHEDULED_UNIT = "storage-viz-scheduled-scan.service"
MAX_PROBE_BYTES = 64 * 1024
GENERATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,240}-[0-9]{1,12}-v1$")


NUMBERS = ('started_unix','finished_unix','updated_unix','scanned_files','scanned_dirs','scanned_bytes','roots_completed','roots_total','last_finished_unix','last_duration_sec','estimate_duration_sec','published_finished_unix','snapshot_finished_unix')
STATES = {'idle','running','complete','partial','failed','unknown'}
PHASES = {'preparing','scanning','publishing'}

def empty_status():
    return dict.fromkeys((*NUMBERS,'phase','current_root','observed_unix','next_run_unix','error_code','generation','snapshot_generation','published_generation','scan_plan','schedule_mode','estimate_max_duration_sec'), None) | {'state':'unknown','timer_state':'unknown','progress_available':False}  # SHIM(M2b..M3b) S2

def validate_lifecycle(value):
    if not isinstance(value, dict):
        raise TransportError('BAD_TELEMETRY','telemetry must be an object')
    result = {}
    for key in NUMBERS:
        v = value.get(key)
        if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0 <= v < 10**18):
            raise TransportError('BAD_TELEMETRY','invalid telemetry counter')
        if key in value: result[key] = v
    for key, limit in (('current_root',4096),('generation',256),('error_code',128),('server_id',128),('run_id',64),('progress_run_id',64)):
        v = value.get(key)
        if v is not None and (not isinstance(v,str) or len(v)>limit or any(ord(c)<32 for c in v)):
            raise TransportError('BAD_TELEMETRY','invalid telemetry text')
        if key in value: result[key] = v
    for key, allowed in (('state',STATES),('phase',PHASES),('scan_plan',{'full','ssd'})):
        if key in value:
            if value[key] is not None and value[key] not in allowed:
                raise TransportError('BAD_TELEMETRY','invalid telemetry state')
            result[key] = value[key]
    return result

def parse_timer(text, *, remote_unix=None, remote_monotonic=None):
    if not isinstance(text,str) or len(text)>8192:
        raise TransportError('BAD_TIMER','timer output is malformed')
    props = dict(line.split('=',1) for line in text.splitlines() if '=' in line)
    state = props.get('ActiveState','unknown')
    result = {'timer_state':state if state in {'active','inactive','failed'} else 'unknown','next_run_unix':None}
    if props.get('Unit') == 'storage-viz-scheduled-scan.service':
        result['schedule_mode'] = 'due-check'
    if state != 'active': return result
    raw = props.get('NextElapseUSecRealtime','')
    try:
        result['next_run_unix'] = int(datetime.strptime(raw,'%a %Y-%m-%d %H:%M:%S %Z').replace(tzinfo=timezone.utc).timestamp())
    except (ValueError,OverflowError): pass
    # systemctl renders timespans as e.g. "3d 2h 14min 5s 200ms".
    mono = props.get('NextElapseUSecMonotonic','')
    units = {'us':.000001,'ms':.001,'s':1,'min':60,'h':3600,'d':86400,'w':604800}
    tokens = re.findall(r'(\d+(?:\.\d+)?)\s*(us|ms|min|s|h|d|w)',mono)
    residue = re.sub(r'(\d+(?:\.\d+)?)\s*(us|ms|min|s|h|d|w)','',mono).strip()
    if tokens and not residue and remote_unix is not None and remote_monotonic is not None:
        deadline = sum(float(n)*units[u] for n,u in tokens)
        candidate = int(remote_unix + deadline - remote_monotonic)
        if candidate > remote_unix:
            result['next_run_unix'] = min(result['next_run_unix'] or candidate,candidate)
    if remote_unix is not None and result['next_run_unix'] is not None and result['next_run_unix'] <= remote_unix:
        result['next_run_unix'] = None
    return result

def merge_status(lifecycle, progress, active, timer, now, *, service_started=None):
    life = validate_lifecycle(lifecycle)
    prog = validate_lifecycle(progress)
    result = empty_status()
    result.update(life)
    result.update(timer)
    result['observed_unix'] = now
    live_start = life.get('started_unix')
    current_lifecycle = life.get('state') == 'running' and not (
        service_started is not None and live_start is not None and live_start < service_started - 2
    )
    if judge.unit_is_active(active):
        result['state'] = 'running'
        if not current_lifecycle:
            for key in ('started_unix','finished_unix','phase','current_root','generation','updated_unix', 'error_code', 'scan_plan', 'estimate_duration_sec', 'scanned_files','scanned_dirs','scanned_bytes','roots_completed','roots_total'):
                result[key] = None
        else:
            progress_id = life.get('progress_run_id', life.get('run_id'))
            if progress_id and prog.get('run_id') == progress_id and (prog.get('updated_unix') or 0) >= (life.get('started_unix') or 0):
                counters = {'roots_completed','scanned_files','scanned_dirs','scanned_bytes'}
                if 'progress_run_id' in life:
                    for key in counters:
                        if prog.get(key) is not None:
                            result[key] = (life.get(key) or 0) + prog[key]
                    result.update({k:prog[k] for k in ('current_root','updated_unix') if prog.get(k) is not None})
                else:
                    result.update({k:v for k,v in prog.items() if k not in {'state','started_unix','finished_unix','generation'}})
                result['progress_available'] = True
            if judge.progress_stale(result.get('updated_unix'), now):
                result['error_code'] = 'STALE_PROGRESS'
                result['progress_available'] = False
        if result.get('started_unix') is None and service_started is not None:
            result['started_unix'] = service_started
    elif active == 'failed':
        result['state'] = 'failed'
        result['error_code'] = life.get('error_code') or 'SERVICE_FAILED'
    elif life.get('state') == 'running':
        result['state'] = 'unknown'; result['error_code'] = 'SCAN_INTERRUPTED'
    elif not life:
        result['state'] = 'idle' if active == 'inactive' else 'unknown'
    if not life or (judge.unit_is_active(active) and not current_lifecycle):
        result.pop('estimate_duration_sec',None)
    result.pop('server_id',None); result.pop('run_id',None); result.pop('progress_run_id',None)
    return result

# Fixed script: no browser/inventory values are interpolated into remote commands or paths.
REMOTE_OBSERVATION_SCRIPT = r'''import hashlib, json, os, re, stat, subprocess, time

def read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): return {'invalid': True}
            data = f.read(16385)
        if len(data)>16384: return {'invalid': True}
        return json.loads(data)
    except FileNotFoundError: return {}
    except Exception: return {'invalid': True}

def show(unit, properties):
    try:
        p = subprocess.run(['/usr/bin/systemctl','show','--property='+properties,*(unit if isinstance(unit,tuple) else (unit,))], env={'LC_ALL':'C','TZ':'UTC','PATH':'/usr/bin:/bin'}, capture_output=True, text=True, timeout=3)
        return p.stdout[:8192] if p.returncode == 0 else ''
    except Exception: return ''

def pointer(path):
    # A v2 host: report the manifest's sha256 and seq, never its body (SHIM(M2b..M3b) S2).
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): return {'invalid': True}
            data = f.read(65537)
    except FileNotFoundError: return None
    except Exception: return {'invalid': True}
    try: seq = json.loads(data).get('seq')
    except Exception: seq = None
    return {'sha256': hashlib.sha256(data).hexdigest(), 'seq': seq if type(seq) is int else None}

def mailbox(path):
    try: return sorted(n for n in os.listdir(path) if re.fullmatch(REQUEST_PATTERN, n))[:16]
    except Exception: return []

manifest = pointer('/var/lib/storage-viz/manifest.json')
run = read('/var/lib/storage-viz/run.json') if manifest is not None else {}
if run and not run.get('invalid'):
    ids = run.get('request_ids')
    run = {'run_id': run.get('run_id'), 'request_ids': ids[:16] if isinstance(ids, list) else []}
v2 = {'manifest': manifest, 'run': run, 'requests_pending': mailbox('/var/lib/storage-viz/requests') if manifest is not None else [],
      'progress': read('/run/storage-viz/progress.json') if manifest is not None else {}}
life = read('/var/lib/storage-viz/scan-lifecycle.json')
published = read('/var/lib/storage-viz/scan-status.json')
progress = read('/run/storage-viz/scan-progress.json')
timer = show('storage-viz-scan.timer','ActiveState,NextElapseUSecRealtime,NextElapseUSecMonotonic,Unit')
# Human-readable systemctl spans use month/year units on long-running hosts.
# Ask D-Bus for the exact integer instead of guessing their duration.
mono = next((line.split('=',1)[1] for line in timer.splitlines() if line.startswith('NextElapseUSecMonotonic=')), '')
if 'month' in mono or 'y' in mono:
    try:
        p = subprocess.run(['/usr/bin/busctl','--system','get-property','org.freedesktop.systemd1','/org/freedesktop/systemd1/unit/storage_2dviz_2dscan_2etimer','org.freedesktop.systemd1.Timer','NextElapseUSecMonotonic'], capture_output=True, text=True, timeout=2)
        match = re.fullmatch(r't ([0-9]{1,20})\s*', p.stdout) if p.returncode == 0 else None
        if match and 0 < int(match.group(1)) < 18446744073709551615:
            timer = '\n'.join(line for line in timer.splitlines() if not line.startswith('NextElapseUSecMonotonic=')) + '\nNextElapseUSecMonotonic=' + match.group(1) + 'us'
    except Exception:
        pass
service_props = 'Id,ActiveState,InvocationID,StateChangeTimestampMonotonic,ExecMainStartTimestampMonotonic,InactiveExitTimestampMonotonic'
services = show(('storage-viz-scan.service', 'storage-viz-scheduled-scan.service'),service_props).strip().split('\n\n')
print(json.dumps({'lifecycle':life,'published':published,'progress':progress,'services':services,'timer':timer,'v2':v2,'remote_unix':time.time(),'remote_monotonic':time.clock_gettime(time.CLOCK_MONOTONIC)}))
'''.replace('REQUEST_PATTERN', repr(REQUEST_ID_RE.pattern))


@dataclass(frozen=True)
class Observation:
    host_now: int
    """The host's clock when it answered."""
    status: Mapping[str, Any]
    """The v1 scan-status fields, as ``merge_status`` derives them."""
    publication: Optional[Mapping[str, Any]]
    """The host's scan-status.json envelope, validated, or None when the host has none."""
    publication_error: Optional[str]
    """Set when scan-status.json exists but is not a valid envelope."""
    scan: Scan
    manifest: Optional[ManifestPointer] = None
    """Set for a v2 host: one with manifest.json. Its v1 files are then left over for rollback (S8)."""
    run: HostRun = HostRun()
    transport: str = "legacy"
    """Which transport answered: ``gate`` or ``legacy`` (S2). Only the gate delivers request ids and updates."""
    agent: AgentFacts = AgentFacts()


@dataclass(frozen=True)
class _Unit:
    name: str
    active: bool
    state: str
    invocation: Optional[str]
    recent: float
    started: float


def _monotonic(props: Mapping[str, str], key: str, observed_monotonic: float) -> float:
    value = props.get(key, "")
    if value.isascii() and value.isdecimal() and len(value) <= 20:
        seconds = int(value) / 1_000_000
        if 0 < seconds <= observed_monotonic:
            return seconds
    return 0


def _units(observation: Mapping[str, Any]) -> list:
    units = []
    observed_monotonic = observation["remote_monotonic"]
    for text in observation.get("services") or [observation.get("service", "")]:
        if not isinstance(text, str):
            raise TransportError("BAD_TELEMETRY", "service state is malformed")
        props = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        state = props.get("ActiveState")
        if state not in KNOWN_ACTIVE_STATES:
            continue
        invocation = props.get("InvocationID", "")
        units.append(_Unit(
            name=props.get("Id", ""),
            active=judge.unit_is_active(state),
            state=state,
            invocation=invocation if RUN_ID_RE.fullmatch(invocation) else None,
            recent=_monotonic(props, "StateChangeTimestampMonotonic", observed_monotonic),
            started=max(_monotonic(props, "ExecMainStartTimestampMonotonic", observed_monotonic),
                        _monotonic(props, "InactiveExitTimestampMonotonic", observed_monotonic)),
        ))
    if not units:
        raise TransportError("ACTIVE_STATE_FAILED", "service state is unavailable")
    return units


def _service_state(observation: Mapping[str, Any], units: list) -> tuple:
    unit = max(units, key=lambda u: (u.active, u.recent, u.state, u.started))
    started = unit.started
    started_unix = int(observation["remote_unix"] - observation["remote_monotonic"] + started) if unit.active and started else None
    return unit.state, started_unix if started_unix is not None and started_unix > 0 else None


def _scan_facts(units: list, lifecycle: Mapping[str, Any], status: Mapping[str, Any]) -> Scan:
    active = [unit for unit in units if unit.active]
    manual = next((unit for unit in active if unit.name == MANUAL_UNIT), None)
    unit = manual or (active[0] if active else None)
    run_id = lifecycle.get("run_id")
    generation = lifecycle.get("generation")
    return Scan(
        invocation=unit.invocation if unit is not None else None,
        full=manual is not None or (unit is not None and status.get("state") == "running" and status.get("scan_plan") == "full"),
        run_id=run_id if isinstance(run_id, str) and RUN_ID_RE.fullmatch(run_id) else None,
        run_state=lifecycle.get("state"),
        run_full=lifecycle.get("scan_plan") == "full",
        generation=generation if isinstance(generation, str) and GENERATION_RE.fullmatch(generation) else None,
        error_code=lifecycle.get("error_code"),
    )


def parse_probe(raw: Any, server: Server) -> Observation:
    if not isinstance(raw, (str, bytes)) or len(raw) > MAX_PROBE_BYTES:
        raise TransportError("BAD_TELEMETRY", "telemetry exceeds maximum size")
    try:
        obj = json.loads(raw)
    except Exception as exc:
        raise TransportError("BAD_TELEMETRY", "telemetry is malformed") from exc
    return parse_telemetry(obj, server, lambda unit_active: _v2_facts(obj.get("v2"), unit_active))


def parse_telemetry(obj: Any, server: Server, v2_facts: Callable[[bool], tuple]) -> Observation:
    """The v1 telemetry the probe prints, which the gate's ``observe`` also carries as ``legacy`` (S7).

    ``v2_facts(unit_active)`` gives a v2 host's (manifest pointer, HostRun, progress.json), or
    (None, HostRun(), None) for a v1 host. The probe and the gate each read them their own way.
    """
    try:
        for key in ("remote_unix", "remote_monotonic"):
            if isinstance(obj[key], bool) or not isinstance(obj[key], (float, int)) or not math.isfinite(obj[key]) or not 0 <= obj[key] < 10**12:
                raise ValueError("clock")
        for key in ("lifecycle", "progress"):
            if not isinstance(obj[key], dict) or obj[key].get("invalid"):
                raise TransportError("BAD_TELEMETRY", "telemetry file is invalid")
        if obj["lifecycle"].get("server_id", server.id) != server.id:
            raise TransportError("IDENTITY_MISMATCH", "telemetry server mismatch")
        lifecycle = validate_lifecycle(obj["lifecycle"])
        units = _units(obj)
        active, started = _service_state(obj, units)
        timer = parse_timer(obj["timer"], remote_unix=obj["remote_unix"], remote_monotonic=obj["remote_monotonic"])
        publication, publication_error = _publication(obj.get("published"), server)
        pointer, run, progress = v2_facts(any(unit.active for unit in units))
        if pointer is not None:
            status = _v2_status(progress, run, active, timer, int(obj["remote_unix"]), started)
            return Observation(int(obj["remote_unix"]), status, publication, publication_error, Scan(), pointer, run)
        status = merge_status(obj["lifecycle"], obj["progress"], active, timer, int(obj["remote_unix"]), service_started=started)
        if publication is not None and publication["status"] in {"complete", "partial"}:
            status["published_generation"] = publication["generation"].removesuffix(".json")  # SHIM(M2b..M3b) S2
            status["published_finished_unix"] = publication["scan_finished_unix"]
        return Observation(int(obj["remote_unix"]), status, publication, publication_error, _scan_facts(units, lifecycle, status))
    except TransportError:
        raise
    except Exception as exc:
        raise TransportError("BAD_TELEMETRY", "telemetry is malformed") from exc


def _v2_facts(v2: Any, unit_active: bool) -> tuple:
    """The manifest pointer, run facts and progress of a v2 host as the probe reports them, or (None, HostRun(), None)."""
    if v2 is None or v2.get("manifest") is None:
        return None, HostRun(), None
    manifest, run = v2["manifest"], v2["run"]
    if manifest.get("invalid") or not HEX64_RE.fullmatch(manifest["sha256"]) or (
            manifest["seq"] is not None and type(manifest["seq"]) is not int):
        raise TransportError("BAD_TELEMETRY", "manifest pointer is invalid")
    if not isinstance(run, dict) or run.get("invalid"):
        raise TransportError("BAD_TELEMETRY", "run.json is invalid")
    ids = run.get("request_ids", [])
    pending = v2["requests_pending"]
    if not all(isinstance(item, str) and REQUEST_ID_RE.fullmatch(item) for item in [*ids, *pending]):
        raise TransportError("BAD_TELEMETRY", "request ids are invalid")
    run_id = run.get("run_id")
    present = bool(run)
    return ManifestPointer(manifest["sha256"], manifest["seq"]), HostRun(
        present=present, scanning=present and unit_active,
        run_id=run_id if isinstance(run_id, str) and RUN_ID_RE.fullmatch(run_id) else None,
        request_ids=frozenset(ids), pending=frozenset(pending), **HostRun.activity_fields(run)), v2.get("progress")


def _v2_status(progress: Any, run: HostRun, active: str, timer: Mapping[str, Any], now: int,
               started: Optional[int]) -> dict:
    """Observation facts for the actor from run.json and progress.json."""
    status = merge_status({}, {}, active, timer, now, service_started=started)
    status.update(run_id=run.run_id, trigger=run.trigger, group=run.group)
    if run.started_unix is not None:
        status["started_unix"] = run.started_unix
    if run.present and not run.scanning:
        status.update(state="unknown", error_code="SCAN_INTERRUPTED")
    if run.scanning and isinstance(progress, dict) and run.run_id is not None and progress.get("run_id") == run.run_id:
        counters = validate_lifecycle({
            "scanned_bytes": progress.get("bytes"), "scanned_files": progress.get("files"),
            "scanned_dirs": progress.get("dirs"), "roots_completed": progress.get("roots_completed"),
            "roots_total": progress.get("roots_total"), "updated_unix": progress.get("updated_unix"),
        })
        status.update(counters, progress_available=True)
        if judge.progress_stale(status.get("updated_unix"), now):
            status.update(error_code="STALE_PROGRESS", progress_available=False)
    return status


def _publication(published: Any, server: Server) -> tuple:
    if published is None or published == {}:
        return None, None
    if not isinstance(published, dict) or published.get("invalid"):
        return None, "BAD_STATUS"
    try:
        return validate_status_envelope(published, server), None
    except TransportError as exc:
        return None, exc.code


class LegacyHost:
    """SHIM(M2b..M3b) S2: today's protocol, as the calls the actor makes, for hosts not bootstrapped yet."""

    def __init__(self, transport: OpenSshTransport) -> None:
        self.transport = transport

    def observe(self, server: Server) -> Observation:
        return parse_probe(self.transport.run_probe(server), server)

    def fetch_capture(self, server: Server, publication: Mapping[str, Any]) -> bytes:
        return self.transport.fetch_snapshot(server, expected_status=publication)[1]

    def get_manifest(self, server: Server) -> bytes:
        return self.transport.fetch_manifest(server)

    def get_capture(self, server: Server, volume_id: str, sha256: str, cap: int) -> bytes:
        return self.transport.fetch_volume_capture(server, volume_id, sha256, cap)

    # A request id needs a write into requests/, which only the gate makes for this account.
    # The actor refuses a v2 rescan at admission on this transport, so this is never reached.
    def rescan(self, server: Server, request_id: str) -> RescanReply:
        return RescanReply("rejected", "REQUEST_UNSUPPORTED")

    def start_rescan(self, server: Server) -> None:
        """SHIM(M2b..M5c) S4. Blocks until the oneshot scan unit finishes: hours for a full scan."""
        self.transport.start_rescan(server)


def legacy_volume(payload, original_sha):
    """SHIM(M2b..M5c) S3: expose the retained host capture as one read-only volume."""
    import gzip
    import hashlib
    from collector import contract
    from collector.view import encode

    vid = "v-" + hashlib.sha256(("legacy:" + payload["server_id"]).encode()).hexdigest()[:16]
    refs = []
    roots = []
    selected = {r["mount_id"]: r for r in payload["selected_roots"]}
    def tree(node):
        return {**node, "kind": "dir" if node["kind"] == "directory" else node["kind"],
                "children": [tree(child) for child in node.get("children", [])]}
    for mount in payload["mounts"]:
        source = selected[mount["mount_id"]]
        path = source["scan_root"]
        binding = "b-" + hashlib.sha256(encode([source.get(k) for k in (
            "major_minor", "mount_source", "mount_root", "mountpoint", "scan_root", "prune_class")])).hexdigest()[:16]
        ref = {"binding": binding, "scan_root": path, "prune_class": "legacy"}
        refs.append(ref)
        rows = {}
        for key in ("top_files", "stale"):
            rows[key] = [{k: ("dir" if k == "kind" and v == "directory" else v) for k,v in row.items()
                          if k in {"path", "uid", "mtime", "bytes", "path_b64"} or (key == "stale" and k == "kind")}
                         for row in payload.get(key, []) if row["path"].startswith(path.rstrip("/") + "/")]
        roots.append({**ref, **rows, "outcome": source["status"], "tree": tree(mount["tree"]),
                      "counts": {"bytes": mount["scanned_bytes"], "files": mount["scanned_files"],
                                 "dirs": mount["scanned_dirs"], "errors": mount["errors"], "vanished_dirs": 0},
                      "errors": [], "users": [{"uid": u["uid"], "name": u["name"], "bytes": u["by_mount"][path],
                                                "files": u["files"] if len(payload["mounts"]) == 1 else 0}
                                               for u in payload["users"] if path in u["by_mount"]]})
    outcome = "partial" if any(r["outcome"] != "complete" for r in roots) else "complete"
    times = {k: payload[k] for k in ("scan_started_unix", "scan_finished_unix")}
    doc = dict(schema=contract.CAPTURE_SCHEMA, volume_id=vid, run_id=original_sha[:32], group="legacy",
               roots=roots, outcome=outcome, **times)
    raw = gzip.compress(encode(doc), mtime=0)
    sha = hashlib.sha256(raw).hexdigest()
    expected = contract.Expected(vid, sha, frozenset((r["scan_root"], r["binding"]) for r in refs))
    capture = contract.parse_capture(raw, expected)
    volume = contract.Volume(vid, "legacy", None, "/", "", "unknown", "unresolved", "legacy",
                             judge.LOCAL_SCAN_CADENCE_SECONDS, tuple(contract.RootRef(**r) for r in refs),
                             None, None)
    table = {"status":"unreadable", "observed_unix":payload["scan_finished_unix"], "within_roots":[]}
    manifest = contract.HostManifest(original_sha, {"volumes":[{"roots":refs,"last_attempt":None}]},
        payload["server_id"], payload["hostname"], 0, payload["scan_finished_unix"], payload["config_digest"],
        (volume,), table, None, ())
    return manifest, {vid:(sha, capture)}, raw
