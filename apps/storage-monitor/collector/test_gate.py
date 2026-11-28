"""The gate transport and host interface against the real gate (agent/bootstrap/gate.py), run in process."""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import io
import json
import os
import pathlib
import signal
import subprocess
import sys

import pytest

from agent.bootstrap import gate as host_gate
from collector.gate import GateHost, parse_observe, parse_timer
from collector.test_bug_harness import make_server
from collector.transport import GATE_VERBS, MAX_GATE_HEADER_BYTES, NOT_GATE, GateTransport, TransportError

REQUEST = "0123456789abcdef0123456789abcdef"
SHA = "d" * 40
OLD = "c" * 40
VOLUME = "v-0123456789abcdef"


class Units(host_gate.Host):
    """systemd as the gate sees it, without systemd."""

    def __init__(self):
        self.started = []
        self.active = {}
        self.health = "ok"

    def start_unit(self, unit):
        self.started.append(unit)
        return True

    def show(self, units, properties):
        blocks = []
        for unit in units:
            state, invocation = self.active.get(unit, ("inactive", ""))
            blocks.append(f"Id={unit}\nActiveState={state}\nInvocationID={invocation}\n"
                          "StateChangeTimestampMonotonic=0\nExecMainStartTimestampMonotonic=0")
        return "\n\n".join(blocks)

    def agent_health(self, paths):
        return self.health


class GateRunner:
    """Stands in for ssh: runs the real gate's ``serve`` on the remote command, under the file-size limit."""

    def __init__(self, root: pathlib.Path):
        self.paths = host_gate.Paths(root)
        for directory in (self.paths.data, self.paths.run, self.paths.requests, self.paths.incoming, self.paths.captures):
            directory.mkdir(parents=True, exist_ok=True)
        self.paths.scan_lock.touch()
        self.units = Units()
        self.calls = []
        self.reply = None
        """Replaces the gate's reply: (returncode, stdout bytes, stderr bytes)."""

    def run(self, argv, *, stdout, stderr, timeout, check, shell, input=None, stdin=None):
        assert shell is False and check is False
        assert argv[:4] == [sys.executable, "-I", "-S", "-c"] and argv[6] == "ssh"
        limit = int(argv[5])
        command = argv[-1]
        self.calls.append({"argv": argv, "command": command, "limit": limit, "timeout": timeout,
                           "input": input, "stdin": stdin})
        if self.reply is not None:
            code, out, err = self.reply
        else:
            buffer = io.BytesIO()
            code = host_gate.serve(command, self.paths, self.units, io.BytesIO(input or b""), buffer)
            out, err = buffer.getvalue(), b""
        stdout.write(out[:limit])
        stderr.write(err)
        stdout.flush()
        return subprocess.CompletedProcess(argv, -signal.SIGXFSZ if len(out) > limit else code)


@pytest.fixture
def remote(tmp_path):
    return GateRunner(tmp_path / "host")


@pytest.fixture
def transport(remote, tmp_path):
    temp = tmp_path / "central-tmp"
    temp.mkdir()
    return GateTransport(runner=remote, temp_dir=temp)


SERVER = make_server("alpha-1")


def write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ------------------------------------------------------------------ run_verb


def test_run_verb_sends_one_verb_with_the_servers_key_strict_known_hosts_and_timeouts(transport, remote):
    write(remote.paths.data / "manifest.json", b'{"seq": 3}')
    assert transport.run_verb(SERVER, "get-manifest") == b'{"seq": 3}'
    call = remote.calls[-1]
    ssh = call["argv"][6:]
    assert ssh[-3:] == ["--", SERVER.host, "get-manifest"], "exactly one verb as the remote command"
    options = " ".join(ssh)
    for expected in ("BatchMode=yes", "StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "ConnectTimeout=10",
                     f"UserKnownHostsFile={SERVER.known_hosts_file}", "User=monitoring", f"-i {SERVER.identity_file}",
                     f"-p {SERVER.port}"):
        assert expected in options
    assert call["stdin"] is subprocess.DEVNULL, "a verb without input never reads central's stdin"
    assert call["limit"] == MAX_GATE_HEADER_BYTES + GATE_VERBS["get-manifest"].cap
    assert call["timeout"] == GATE_VERBS["get-manifest"].timeout


@pytest.mark.parametrize("verb,args", [
    ("observe", ("x",)), ("get-capture", (VOLUME, "../etc/passwd")), ("rescan", ("id; reboot",)),
    ("stage-update", (SHA, "0")), ("activate-update", (SHA.upper(),)), ("sh", ()), ("get-capture", (VOLUME,)),
])
def test_a_call_outside_the_verb_grammar_never_reaches_ssh(transport, remote, verb, args):
    with pytest.raises(TransportError) as raised:
        transport.run_verb(SERVER, verb, args)
    assert raised.value.code == "BAD_VERB" and remote.calls == []


def test_unsafe_inventory_never_reaches_ssh(transport, remote):
    for server in (dataclasses.replace(SERVER, host="a;b"), dataclasses.replace(SERVER, identity_file=pathlib.PurePosixPath("/tmp/k"))):
        with pytest.raises(TransportError) as raised:
            transport.run_verb(server, "observe")
        assert raised.value.code == "BAD_INVENTORY"
    assert remote.calls == []


def test_gate_refusals_keep_their_code(transport, remote):
    for verb, args, code in (("get-capture", (VOLUME, "e" * 64), "CAPTURE_GONE"), ("get-manifest", (), "MANIFEST_MISSING")):
        with pytest.raises(TransportError) as raised:
            transport.run_verb(SERVER, verb, args, cap=10) if verb == "get-capture" else transport.run_verb(SERVER, verb)
        assert raised.value.code == code


@pytest.mark.parametrize("reply,code", [
    ((127, b"", b"sh: 1: observe: not found\n"), NOT_GATE),
    ((0, b'{"schema": "storage-viz.observe/1"}\n', b""), NOT_GATE),
    ((255, b"", b"monitoring@alpha: Permission denied (publickey).\n"), "AUTH_FAILED"),
    ((255, b"", b"Host key verification failed.\n"), "AUTH_FAILED"),
    ((255, b"", b"ssh: connect to host alpha port 22: No route to host\n"), "UNREACHABLE"),
    ((0, b"storage-viz-gate/1 ok 10\nshort", b""), "BAD_REPLY"),
    ((0, b"storage-viz-gate/1 ok 2\nlonger", b""), "BAD_REPLY"),
    ((1, b"storage-viz-gate/1 ok 2\nok", b""), "BAD_REPLY"),
    ((0, b"storage-viz-gate/1 ok 70000\n", b""), "REPLY_TOO_LARGE"),
    ((1, b"storage-viz-gate/1 err IO_ERROR\n", b""), "IO_ERROR"),
])
def test_replies_are_classified_into_link_classes(transport, remote, reply, code):
    remote.reply = reply
    with pytest.raises(TransportError) as raised:
        transport.run_verb(SERVER, "observe")
    assert raised.value.code == code


def test_a_capture_body_must_have_the_declared_size_and_stay_under_the_cap(transport, remote):
    sha = "e" * 64
    write(remote.paths.captures / VOLUME / f"{sha}.json.gz", b"x" * 100)
    assert transport.run_verb(SERVER, "get-capture", (VOLUME, sha), cap=100) == b"x" * 100
    with pytest.raises(TransportError) as raised:
        transport.run_verb(SERVER, "get-capture", (VOLUME, sha), cap=99)
    assert raised.value.code == "SIZE_MISMATCH", "a file larger than its declared size gets one clean retry"
    with pytest.raises(TransportError) as raised:
        transport.run_verb(SERVER, "get-capture", (VOLUME, sha), cap=101)
    assert raised.value.code == "SIZE_MISMATCH"
    assert remote.calls[-1]["limit"] == MAX_GATE_HEADER_BYTES + 101, "the reply file is capped at the declared size"


def test_output_past_the_file_limit_is_too_large(transport, remote):
    remote.reply = (0, b"storage-viz-gate/1 ok 999999\n" + b"x" * 999999, b"")
    with pytest.raises(TransportError) as raised:
        transport.run_verb(SERVER, "get-capture", (VOLUME, "e" * 64), cap=100)
    assert raised.value.code == "CAPTURE_TOO_LARGE"


def test_timeouts_and_spawn_failures_are_transport_failures(tmp_path):
    class Failing:
        def __init__(self, exc):
            self.exc = exc

        def run(self, argv, **kwargs):
            raise self.exc

    for exc, code in ((subprocess.TimeoutExpired("ssh", 30), "TIMEOUT"), (OSError("no ssh"), "UNREACHABLE")):
        with pytest.raises(TransportError) as raised:
            GateTransport(runner=Failing(exc), temp_dir=tmp_path).run_verb(SERVER, "observe")
        assert raised.value.code == code
    assert list(tmp_path.iterdir()) == [], "temporary reply files are removed"


# ------------------------------------------------------------------ GateHost


def gate_host(transport):
    return GateHost(transport)


def test_rescan_delivers_the_request_id_and_reads_the_gates_answer(transport, remote):
    host = gate_host(transport)
    assert host.rescan(SERVER, REQUEST).outcome == "received"
    assert (remote.paths.requests / REQUEST).exists()
    assert remote.units.started == ["storage-viz-scan.service"]
    assert host.rescan(SERVER, REQUEST).outcome == "already_received"
    remote.reply = (0, b"storage-viz-gate/1 ok 26\nrejected TOO_MANY_PENDING\n", b"")
    reply = host.rescan(SERVER, REQUEST)
    assert (reply.outcome, reply.code) == ("rejected", "TOO_MANY_PENDING")
    remote.reply = (0, b"storage-viz-gate/1 ok 4\nwhat", b"")
    with pytest.raises(TransportError) as raised:
        host.rescan(SERVER, REQUEST)
    assert raised.value.code == "BAD_REPLY", "an unparsable answer is ambiguous to the job, never a success"


def test_update_verbs_stage_the_bundle_then_its_signature_and_request_the_updater(transport, remote):
    host = gate_host(transport)
    host.stage_update(SERVER, SHA, b"bundle-bytes", b"signature")
    assert (remote.paths.incoming / f"{SHA}.tar.gz").read_bytes() == b"bundle-bytes"
    assert (remote.paths.incoming / f"{SHA}.tar.gz.sig").read_bytes() == b"signature"
    assert remote.calls[-1]["command"] == f"stage-update {SHA} 12"
    host.activate_update(SERVER, SHA)
    assert (remote.paths.incoming / "activate").read_text() == f"activate {SHA}\n"
    host.rollback_update(SERVER, OLD)
    assert (remote.paths.incoming / "activate").read_text() == f"rollback {OLD}\n"
    assert remote.units.started == ["storage-viz-agent-update.service"] * 2


# ------------------------------------------------------------------ observe


def observe(transport, server=SERVER):
    return gate_host(transport).observe(server)


def manifest_bytes(seq=7):
    return json.dumps({"schema": "storage-viz.manifest/2", "seq": seq}).encode()


def test_a_v2_host_reports_its_manifest_pointer_and_scan_state_from_lock_and_run_json(transport, remote):
    data = manifest_bytes()
    write(remote.paths.data / "manifest.json", data)
    observation = observe(transport)
    assert (observation.manifest.sha256, observation.manifest.seq) == (hashlib.sha256(data).hexdigest(), 7)
    assert (observation.run.present, observation.run.scanning) == (False, False)
    write(remote.paths.requests / REQUEST, b"")
    assert observe(transport).run.pending == frozenset({REQUEST})
    run = {"schema": "storage-viz.run/2", "run_id": "b" * 32, "request_ids": [REQUEST]}
    write(remote.paths.data / "run.json", json.dumps(run).encode())
    interrupted = observe(transport)
    assert (interrupted.run.present, interrupted.run.scanning) == (True, False)
    assert interrupted.status["error_code"] == "SCAN_INTERRUPTED"
    with open(remote.paths.scan_lock, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        running = observe(transport)
    assert (running.run.present, running.run.scanning, running.run.run_id) == (True, True, "b" * 32)
    assert running.run.request_ids == frozenset({REQUEST})


def test_observe_reports_the_agent_release_its_health_and_the_updaters_last_attempt(transport, remote):
    releases = remote.paths.opt / "releases" / SHA
    releases.mkdir(parents=True)
    os.symlink(f"releases/{SHA}", remote.paths.opt / "current")
    attempt = {"action": "activate", "sha": SHA, "outcome": "refused", "code": "FLOOR", "finished_unix": 1700000000}
    write(remote.paths.update / "state.json", json.dumps({"active": SHA, "last_attempt": attempt}).encode())
    remote.units.health = "broken"
    agent = observe(transport).agent
    assert agent.release_sha == SHA and agent.broken
    assert (agent.attempt.action, agent.attempt.sha, agent.attempt.outcome, agent.attempt.code) == ("activate", SHA, "refused", "FLOOR")
    write(remote.paths.update / "state.json", b"{not json")
    assert observe(transport).agent.attempt is None, "an unusable state.json reads as no attempt"


class UnitlessHealth(host_gate.UnitlessHost):
    def agent_health(self, paths):
        return "ok"


def test_a_truenas_host_without_units_reads_as_a_v2_host(transport, remote, tmp_path):
    """`gate.py --root DIR --no-units`: no systemd blocks, the cron job's lock and run.json still decide."""
    remote.paths, _ = host_gate.options(["--root", str(tmp_path / "nexus"), "--no-units"])
    remote.units = UnitlessHealth()
    for directory in (remote.paths.requests, remote.paths.run):
        directory.mkdir(parents=True)
    remote.paths.scan_lock.touch()
    write(remote.paths.data / "manifest.json", manifest_bytes())
    idle = observe(transport)
    assert (idle.manifest.seq, idle.run.scanning, idle.agent.broken) == (7, False, False)
    assert idle.status["state"] != "running"
    run = {"schema": "storage-viz.run/2", "run_id": "b" * 32, "request_ids": []}
    write(remote.paths.data / "run.json", json.dumps(run).encode())
    with open(remote.paths.scan_lock, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        running = observe(transport)
    assert (running.run.scanning, running.run.run_id, running.status["state"]) == (True, "b" * 32, "running")
    assert gate_host(transport).rescan(SERVER, REQUEST).outcome == "received"
    assert (remote.paths.requests / REQUEST).exists()


def test_a_systemd_host_whose_units_are_unreadable_still_fails_active_state(transport, remote):
    remote.units.show = lambda units, properties: ""
    write(remote.paths.data / "manifest.json", manifest_bytes())
    with pytest.raises(TransportError) as caught:
        observe(transport)
    assert caught.value.code == "ACTIVE_STATE_FAILED", "only a cron host's gate lets the lock stand in for units"


def test_observe_refuses_malformed_replies_as_bad_telemetry(transport, remote):
    good = json.loads(host_gate.observe(remote.paths, remote.units))
    for broken in ({**good, "schema": "x"}, {**good, "scan": {"state": "phase", "run": None}, "manifest": {"seq": 1, "sha256": "0" * 64}},
                   {**good, "manifest": {"seq": 1, "sha256": "zz"}}, {**good, "agent": {"release_sha": "x", "health": "ok"}}):
        with pytest.raises(TransportError) as raised:
            parse_observe(json.dumps(broken).encode(), SERVER)
        assert raised.value.code == "BAD_TELEMETRY"
    with pytest.raises(TransportError):
        parse_observe(b"\xff", SERVER)


# ------------------------------------------------------------------ transport choice


@pytest.mark.parametrize("host_code", ["NOT_GATE", "TIMEOUT", "AUTH_FAILED", "UNREACHABLE", "SOMETHING_NEW"])
def test_a_host_cannot_claim_centrals_own_codes(transport, remote, host_code):
    remote.reply = (1, f"storage-viz-gate/1 err {host_code}\n".encode(), b"")
    with pytest.raises(TransportError) as raised:
        transport.run_verb(SERVER, "observe")
    assert (raised.value.code, raised.value.host_code) == ("GATE_REFUSED", host_code)


def test_the_gates_own_refusals_keep_their_code_and_the_host_text(transport, remote):
    remote.reply = (1, b"storage-viz-gate/1 err START_FAILED\n", b"")
    with pytest.raises(TransportError) as raised:
        transport.run_verb(SERVER, "activate-update", (SHA,))
    assert (raised.value.code, raised.value.host_code) == ("START_FAILED", "START_FAILED")


def test_an_updater_code_that_is_not_a_code_reads_as_no_attempt(transport, remote):
    attempt = {"action": "activate", "sha": SHA, "outcome": "refused", "code": "floor; rm -rf", "finished_unix": 1}
    write(remote.paths.update / "state.json", json.dumps({"last_attempt": attempt}).encode())
    assert observe(transport).agent.attempt is None


def test_bootstrap_one_observe_still_supplies_service_and_timer_details(remote):
    report = json.loads(host_gate.observe(remote.paths, remote.units))
    expected = parse_observe(json.dumps(report).encode(), SERVER)
    report["bootstrap_version"] = 1
    report["legacy"] = dict(report.pop("telemetry"), published={}, lifecycle={}, progress={})
    assert parse_observe(json.dumps(report).encode(), SERVER) == expected


def test_timer_uses_the_host_clock_for_both_absolute_and_monotonic_deadlines():
    assert parse_timer("ActiveState=active\nNextElapseUSecRealtime=Mon 2026-11-16 12:00:00 UTC")["next_run_unix"] == 1794830400
    timer = "ActiveState=active\nUnit=storage-viz-scheduled-scan.service\nNextElapseUSecMonotonic=1h 2min 3s"
    assert parse_timer(timer, remote_unix=10000, remote_monotonic=3600) == {
        "timer_state": "active", "schedule_mode": "due-check", "next_run_unix": 10123}
    assert parse_timer(timer.replace("=active", "=inactive"))["next_run_unix"] is None
