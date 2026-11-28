"""Gate verbs and the service telemetry returned by observe."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any, Mapping, Optional

from collector import contract, judge
from collector.contract import AgentFacts, HostRun, ManifestPointer
from collector.inventory import Server
from collector.jobs import RescanReply
from collector.transport import KNOWN_ACTIVE_STATES, GateTransport, TransportError


@dataclass(frozen=True)
class Observation:
    host_now: int
    status: Mapping[str, Any]
    manifest: Optional[ManifestPointer]
    run: HostRun
    agent: AgentFacts


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

@dataclass(frozen=True)
class _Unit:
    active: bool
    state: str
    recent: float
    started: float


def _monotonic(props: Mapping[str, str], key: str, observed_monotonic: float) -> float:
    value = props.get(key, "")
    if value.isascii() and value.isdecimal() and len(value) <= 20:
        seconds = int(value) / 1_000_000
        if 0 < seconds <= observed_monotonic:
            return seconds
    return 0


def _units(services, observed_monotonic):
    units = []
    for text in services:
        if not isinstance(text, str):
            raise TransportError("BAD_TELEMETRY", "service state is malformed")
        props = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        state = props.get("ActiveState")
        if state not in KNOWN_ACTIVE_STATES:
            continue
        units.append(_Unit(
            active=judge.unit_is_active(state),
            state=state,
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


def _v2_status(progress: Any, run: HostRun, active: str, timer: Mapping[str, Any], now: int,
               started: Optional[int]) -> dict:
    """Observation facts for the actor from run.json and progress.json."""
    state = "running" if judge.unit_is_active(active) else "failed" if active == "failed" else "idle" if active == "inactive" else "unknown"
    status = dict(timer, state=state, observed_unix=now, started_unix=started, progress_available=False,
                  error_code="SERVICE_FAILED" if state == "failed" else None)
    status.update(run_id=run.run_id, trigger=run.trigger, group=run.group)
    if run.started_unix is not None:
        status["started_unix"] = run.started_unix
    if run.present and not run.scanning:
        status.update(state="unknown", error_code="SCAN_INTERRUPTED")
    if run.scanning and isinstance(progress, dict) and run.run_id is not None and progress.get("run_id") == run.run_id:
        counters = {
            "scanned_bytes": progress.get("bytes"), "scanned_files": progress.get("files"),
            "scanned_dirs": progress.get("dirs"), "roots_completed": progress.get("roots_completed"),
            "roots_total": progress.get("roots_total"), "updated_unix": progress.get("updated_unix"),
        }
        for value in counters.values():
            if value is not None and (type(value) not in (int, float) or not 0 <= value < 10**18):
                raise ValueError("invalid progress counter")
        status.update(counters, progress_available=True)
        if judge.progress_stale(status.get("updated_unix"), now):
            status.update(error_code="STALE_PROGRESS", progress_available=False)
    return status


def parse_observe(raw: bytes, server: Server) -> Observation:
    try:
        facts = contract.parse_observation(raw)
        section = facts.telemetry
        now, mono = section["remote_unix"], section["remote_monotonic"]
        for value in (now, mono):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 10**12:
                raise ValueError("invalid host clock")
        if facts.cron:
            active, started = ("active" if facts.run.scanning else "inactive"), None
        else:
            active, started = _service_state(section, _units(section["services"], mono))
        timer = parse_timer(section["timer"], remote_unix=now, remote_monotonic=mono)
        status = _v2_status(facts.progress, facts.run, active, timer, int(now), started)
        return Observation(int(now), status, facts.manifest, facts.run, facts.agent)
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise TransportError("BAD_TELEMETRY", "invalid observe reply") from exc


class GateHost:
    """The host interface (``actor.Host``) over the gate."""

    def __init__(self, transport: GateTransport) -> None:
        self.transport = transport

    def observe(self, server: Server) -> Observation:
        return parse_observe(self.transport.run_verb(server, "observe"), server)

    def get_manifest(self, server: Server) -> bytes:
        return self.transport.run_verb(server, "get-manifest")

    def get_capture(self, server: Server, volume_id: str, sha256: str, cap: int) -> bytes:
        return self.transport.run_verb(server, "get-capture", (volume_id, sha256), cap=cap)

    def rescan(self, server: Server, request_id: str) -> RescanReply:
        body = self.transport.run_verb(server, "rescan", (request_id,))
        words = body.decode("ascii", "replace").split()
        if words in (["received"], ["already_received"]):
            return RescanReply(words[0])
        if len(words) == 2 and words[0] == "rejected":
            return RescanReply("rejected", words[1][:64])
        raise TransportError("BAD_REPLY", "unparsable rescan reply")

    def stage_update(self, server: Server, sha: str, bundle: bytes, signature: bytes) -> None:
        self._expect(self.transport.run_verb(server, "stage-update", (sha, str(len(bundle))), stdin=bundle + signature),
                     b"staged\n")

    def activate_update(self, server: Server, sha: str) -> None:
        self._expect(self.transport.run_verb(server, "activate-update", (sha,)), b"requested\n")

    def rollback_update(self, server: Server, sha: str) -> None:
        self._expect(self.transport.run_verb(server, "rollback-update", (sha,)), b"requested\n")

    @staticmethod
    def _expect(body: bytes, expected: bytes) -> None:
        if body != expected:
            raise TransportError("BAD_REPLY", "unexpected gate reply")
