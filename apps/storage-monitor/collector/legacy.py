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
from typing import Any, Mapping, Optional

from collector import judge
from collector.inventory import Server
from collector.jobs import Scan
from collector.transport import KNOWN_ACTIVE_STATES, OpenSshTransport, TransportError, validate_status_envelope

MANUAL_UNIT = "storage-viz-scan.service"
SCHEDULED_UNIT = "storage-viz-scheduled-scan.service"
MAX_PROBE_BYTES = 48 * 1024
INVOCATION_RE = re.compile(r"^[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
GENERATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,240}-[0-9]{1,12}-v1$")


NUMBERS = ('started_unix','finished_unix','updated_unix','scanned_files','scanned_dirs','scanned_bytes','roots_completed','roots_total','last_finished_unix','last_duration_sec','estimate_duration_sec','published_finished_unix','snapshot_finished_unix')
STATES = {'idle','running','complete','partial','failed','unknown'}
PHASES = {'preparing','scanning','publishing'}

def empty_status():
    return dict.fromkeys((*NUMBERS,'phase','current_root','observed_unix','next_run_unix','error_code','generation','snapshot_generation','published_generation','scan_plan','schedule_mode','estimate_max_duration_sec'), None) | {'state':'unknown','timer_state':'unknown','progress_available':False}

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
REMOTE_OBSERVATION_SCRIPT = r'''import json, os, re, stat, subprocess, time

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
print(json.dumps({'lifecycle':life,'published':published,'progress':progress,'services':services,'timer':timer,'remote_unix':time.time(),'remote_monotonic':time.clock_gettime(time.CLOCK_MONOTONIC)}))
'''


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
            invocation=invocation if INVOCATION_RE.fullmatch(invocation) else None,
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
        status = merge_status(obj["lifecycle"], obj["progress"], active, timer, int(obj["remote_unix"]), service_started=started)
        publication, publication_error = _publication(obj.get("published"), server)
        if publication is not None and publication["status"] in {"complete", "partial"}:
            status["published_generation"] = publication["generation"].removesuffix(".json")
            status["published_finished_unix"] = publication["scan_finished_unix"]
        return Observation(int(obj["remote_unix"]), status, publication, publication_error, _scan_facts(units, lifecycle, status))
    except TransportError:
        raise
    except Exception as exc:
        raise TransportError("BAD_TELEMETRY", "telemetry is malformed") from exc


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
    """SHIM(M2b..M3b) S2: today's protocol, as the three calls the actor makes."""

    def __init__(self, transport: OpenSshTransport) -> None:
        self.transport = transport

    def observe(self, server: Server) -> Observation:
        return parse_probe(self.transport.run_probe(server), server)

    def fetch_capture(self, server: Server, publication: Mapping[str, Any]) -> bytes:
        return self.transport.fetch_snapshot(server, expected_status=publication)[1]

    def start_rescan(self, server: Server) -> None:
        """Blocks until the oneshot scan unit finishes: hours for a full scan."""
        self.transport.start_rescan(server)
