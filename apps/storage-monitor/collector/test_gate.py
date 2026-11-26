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
from collector.gate import GateHost, HostRouter, parse_observe
from collector.legacy import Observation
from collector.test_bug_harness import make_server
from collector.test_snapshot import base_payload, status_for
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


def test_s7_v1_snapshot_is_read_with_get_snapshot_and_checked_against_get_status(transport, remote):
    payload = base_payload(server_id=SERVER.id, generation=f"{SERVER.id}-1719200000-v1")
    status, data = status_for(payload, config_digest="a" * 64)
    write(remote.paths.data / "snapshots" / status["generation"], data)
    write(remote.paths.data / "scan-status.json", json.dumps(status).encode())
    host = gate_host(transport)
    assert host.fetch_capture(SERVER, status) == data
    changed = dict(status, sha256="f" * 64)
    with pytest.raises(TransportError) as raised:
        host.fetch_capture(SERVER, changed)
    assert raised.value.code == "RACE"


# ------------------------------------------------------------------ observe


def observe(transport, server=SERVER):
    return gate_host(transport).observe(server)


def v1_files(remote):
    payload = base_payload(server_id=SERVER.id, generation=f"{SERVER.id}-1719200000-v1")
    status, _ = status_for(payload, config_digest="a" * 64)
    write(remote.paths.data / "scan-status.json", json.dumps(status).encode())
    write(remote.paths.data / "scan-lifecycle.json", json.dumps({"state": "complete", "server_id": SERVER.id}).encode())
    return status


def test_a_v1_agent_behind_the_gate_reads_as_it_does_over_s2(transport, remote):
    status = v1_files(remote)
    observation = observe(transport)
    assert observation.transport == "gate"
    assert observation.manifest is None and observation.publication["sha256"] == status["sha256"]
    assert observation.status["state"] == "complete", "the v1 lifecycle file decides, as over S2"
    remote.units.active["storage-viz-scan.service"] = ("active", "a" * 32)
    observation = observe(transport)
    assert observation.status["state"] == "running"
    assert observation.scan.invocation == "a" * 32, "S4 correlates the v1 job by the unit's InvocationID"


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


class Scripted:
    def __init__(self, name):
        self.name = name
        self.errors = []
        self.calls = []

    def observe(self, server):
        self.calls.append("observe")
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise TransportError(error, error)
        return Observation(0, {}, None, None, None, transport=self.name)

    def get_manifest(self, server):
        self.calls.append("get_manifest")
        return self.name.encode()


def router():
    gate, s2 = Scripted("gate"), Scripted("legacy")
    return HostRouter(gate, s2), gate, s2


def test_a_gate_host_in_the_inventory_never_uses_s2():
    hosts, gate, s2 = router()
    server = dataclasses.replace(SERVER, transport="gate")
    gate.errors = [NOT_GATE]
    with pytest.raises(TransportError):
        hosts.observe(server)
    assert hosts.observe(server).transport == "gate" and hosts.get_manifest(server) == b"gate"
    assert s2.calls == [] and hosts.transport(server) == "gate"


def test_a_legacy_host_moves_to_the_gate_once_the_gate_answers_and_back_when_it_is_reverted():
    hosts, gate, s2 = router()
    server = dataclasses.replace(SERVER, transport="legacy")
    gate.errors = [NOT_GATE]
    assert hosts.observe(server).transport == "legacy" and hosts.transport(server) == "legacy"
    assert hosts.observe(server).transport == "legacy"
    assert gate.calls == ["observe"], "the gate is tried on the first contact, not on every S2 observe"
    s2.errors = ["TELEMETRY_UNAVAILABLE"]  # bootstrapped: the forced command refuses the S2 probe
    with pytest.raises(TransportError):
        hosts.observe(server)
    assert hosts.observe(server).transport == "gate" and hosts.transport(server) == "gate"
    assert hosts.get_manifest(server) == b"gate"
    gate.errors = ["UNREACHABLE"]
    with pytest.raises(TransportError):
        hosts.observe(server)
    assert hosts.transport(server) == "gate", "a failed contact proves nothing about the transport"
    gate.errors = [NOT_GATE]  # bootstrap reverted
    assert hosts.observe(server).transport == "legacy" and hosts.transport(server) == "legacy"


def test_a_gate_refusal_on_a_legacy_host_proves_the_gate():
    hosts, gate, s2 = router()
    server = dataclasses.replace(SERVER, transport="legacy")
    gate.errors = ["IO_ERROR"]
    with pytest.raises(TransportError):
        hosts.observe(server)
    assert hosts.transport(server) == "gate" and s2.calls == []


def test_an_unreachable_legacy_host_is_not_contacted_twice_per_observe():
    hosts, gate, s2 = router()
    server = dataclasses.replace(SERVER, transport="legacy")
    gate.errors = ["TIMEOUT"]
    with pytest.raises(TransportError):
        hosts.observe(server)
    assert s2.calls == [] and hosts.transport(server) == "legacy"
