#!/usr/bin/env python3
"""The real collector driving the e2e container host over the gate (M3b).

run-agent-channel-e2e.sh calls this once per phase. Each run builds a Fleet on
the same state directory, so records and rollout.json carry across phases (and
across a restart, as in production). The actor runs in this thread with its
helper work inline, so each step is deterministic; the host side is real: sshd,
the forced command, gate.py, systemd units and storage-viz-update.

Phases:
  data         observe; get-manifest and get-capture of seeded v2 files; a rescan
               whose request id reaches the host and comes back in a receipt.
  update-pass  desired = the bundle in --bundle-dir: the canary stages, activates
               and passes probation on a seeded run; the rollout opens the fleet.
  update-fail  first, the manual rollback since update-pass is a switch central did
               not make, so that release goes through probation; then desired = the
               bundle in --bundle-dir: probation fails on a seeded INTERNAL_* fault;
               central sends rollback-update <previous>, never activate-update, and
               the rollout halts.

The agent's mount policy refuses to scan the container root, so v2 data is
seeded into /var/lib/storage-viz directly instead of scanned.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import pathlib
import subprocess
import sys
import time
import uuid

SID = "e2e-host"
VOLUME, BINDING, ROOT = "v-00000000000000e2", "b-00000000000000e2", "/e2e"


def capture_bytes(run_id: str, started: int, finished: int) -> bytes:
    """A §2.2 VolumeCapture of one empty root, as the agent would gzip it."""
    doc = {"schema": "storage-viz.capture/2", "volume_id": VOLUME, "run_id": run_id, "group": "slow",
           "scan_started_unix": started, "scan_finished_unix": finished, "outcome": "complete",
           "roots": [{"scan_root": ROOT, "binding": BINDING, "prune_class": "data", "outcome": "complete",
                      "counts": {"bytes": 0, "files": 0, "dirs": 0, "errors": 0, "vanished_dirs": 0},
                      "tree": {"name": "e2e", "kind": "dir", "bytes": 0, "files": 0, "uid": 0, "mtime": 0,
                               "other_bytes": 0, "children": []},
                      "users": [], "top_files": [], "stale": [], "errors": []}]}
    return gzip.compress(json.dumps(doc, sort_keys=True).encode(), mtime=0)


def manifest_bytes(seq: int, written: int, last_run: dict, capture: tuple, error_code) -> bytes:
    """A §2.2 HostManifest naming one capture (sha256, bytes), whose volume's last attempt is ``last_run``."""
    digest, size = capture
    volume = {
        "volume_id": VOLUME, "identity": "uuid", "diagnostic": None, "mountpoint": ROOT, "fstype": "ext4",
        "media": "ssd", "media_confidence": "resolved", "group": "slow", "cadence_sec": 3600,
        "roots": [{"scan_root": ROOT, "binding": BINDING, "prune_class": "data"}],
        "capacity": {"total": 1000, "used": 400, "avail": 600, "observed_unix": written, "status": "ok"},
        "capture": {"sha256": digest, "bytes": size, "scan_started_unix": last_run["started_unix"],
                    "scan_finished_unix": last_run["finished_unix"], "outcome": "complete"},
        "last_attempt": {"run_id": last_run["run_id"], "started_unix": last_run["started_unix"],
                         "finished_unix": last_run["finished_unix"], "outcome": last_run["outcome"], "error_code": error_code},
    }
    doc = {"schema": "storage-viz.manifest/2", "server_id": SID, "hostname": SID, "seq": seq, "written_unix": written,
           "agent": {"release_sha": last_run["agent_sha"], "release_seq": 1, "config_digest": "a" * 64},
           "volumes": [volume], "mount_table": {"status": "ok", "observed_unix": written, "within_roots": []},
           "last_run": last_run, "requests": []}
    return json.dumps(doc, sort_keys=True).encode()


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=pathlib.Path, required=True)
    parser.add_argument("--work", type=pathlib.Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--cid", required=True)
    parser.add_argument("--phase", choices=("data", "update-pass", "update-fail"), required=True)
    parser.add_argument("--bundle-dir", type=pathlib.Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.app))
    E2E(args).run()


class E2E:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    # ------------------------------------------------------------ setup

    def run(self) -> None:
        from collector.fleet import Fleet
        from collector.gate import GateHost, HostRouter
        from collector.inventory import Server
        from collector.legacy import LegacyHost
        from collector.transport import GateTransport, OpenSshTransport

        work = self.args.work
        server = Server(id=SID, display_name=SID, order=1, host="127.0.0.1", port=self.args.port, enabled=True,
                        username="monitoring", identity_file=pathlib.PurePosixPath(work / "central"),
                        known_hosts_file=pathlib.PurePosixPath(work / "known_hosts_gate"), scanner={"server_id": SID},
                        scanner_digest="a" * 64, transport="gate", canary=True)
        transport = GateTransport(key_root=f"{work}/", temp_dir=work)
        self.verbs = []
        run_verb = transport.run_verb

        def counted(server, verb, args=(), stdin=None, cap=None):
            self.verbs.append(" ".join((verb, *args)))
            return run_verb(server, verb, args, stdin, cap)

        transport.run_verb = counted
        desired = self.args.bundle_dir / "storage-viz-agent.json" if self.args.bundle_dir else work / "no-agent.json"
        self.fleet = Fleet([server], work / "central-state", HostRouter(GateHost(transport), LegacyHost(OpenSshTransport())),
                           spawn=lambda job, name: job(), desired_path=desired)
        self.actor = self.fleet.actors[SID]
        self.actor._guard(self.actor.load)
        getattr(self, self.args.phase.replace("-", "_"))()

    def step(self) -> None:
        self.actor._next_contact = 0.0
        self.actor.step()
        while not self.actor.inbox.empty():
            self.actor.step()

    def until(self, what: str, check, timeout: float = 180.0, pause: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.step()
            value = check()
            if value:
                return value
            time.sleep(pause)
        record = self.actor._record
        fail(f"{what}: timed out; link={record.link} update={record.update} job={record.job} last_job={record.last_job} "
             f"verbs={self.verbs[-12:]}")

    @property
    def record(self):
        return self.actor._record

    def docker(self, *argv: str, data: bytes = b"") -> str:
        done = subprocess.run(["docker", "exec", "-i", self.args.cid, *argv], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False)
        if done.returncode != 0:
            fail(f"docker exec {argv}: {done.stderr.decode()[-400:]}")
        return done.stdout.decode()

    def host_now(self) -> int:
        return int(self.docker("date", "+%s").strip())

    def seed(self, *, last_run: dict, error_code=None) -> str:
        """Write one v2 capture and a manifest naming it, as the agent would publish them."""
        data = capture_bytes(last_run["run_id"], last_run["started_unix"], last_run["finished_unix"])
        digest = hashlib.sha256(data).hexdigest()
        held = self.record.data.manifest
        current = json.loads(self.docker("cat", "/var/lib/storage-viz/manifest.json"))
        seq = max(current["seq"], getattr(held, "seq", 0)) + 1
        manifest = manifest_bytes(seq, last_run["finished_unix"], last_run, (digest, len(data)), error_code)
        self.write(f"/var/lib/storage-viz/captures/{VOLUME}/{digest}.json.gz", data)
        self.write("/var/lib/storage-viz/manifest.json", manifest)
        return digest

    def write(self, path: str, data: bytes) -> None:
        self.docker("sh", "-c", f'install -D -m 0640 -g storage-viz-collector /dev/stdin "{path}.e2e" && mv -f "{path}.e2e" "{path}"',
                    data=data)

    def run_doc(self, agent_sha: str, outcome: str = "complete") -> dict:
        now = self.host_now()
        return {"run_id": uuid.uuid4().hex, "trigger": "timer", "plan": "full", "started_unix": now,
                "finished_unix": now + 1, "outcome": outcome, "agent_sha": agent_sha}

    def release(self) -> str:
        """The release central shows for the host."""
        return self.actor.view.release_sha

    def host_release(self) -> str:
        """The release the host runs, read on the host."""
        return pathlib.PurePosixPath(self.docker("readlink", "/opt/storage-viz/current").strip()).name

    def summary(self) -> dict:
        from collector.view import summary_v1
        return summary_v1(self.actor.view, self.fleet.clock.monotonic())

    # ------------------------------------------------------------ phases

    def data(self) -> None:
        from collector.actor import Admit, Ask

        self.step()
        row = self.summary()
        if row["link"] != {**row["link"], "state": "ok", "transport": "gate"} or row["agent"]["release_sha"] is None:
            fail(f"observe over the gate: {row['link']} {row['agent']}")
        print(f"PASS: collector observe over the gate: link ok, transport gate, agent release {row['agent']['release_sha'][:8]}")

        digest = self.seed(last_run=self.run_doc(self.release()))
        self.until("seeded capture accepted", lambda: self.record.data.accepted.get(VOLUME) == digest, timeout=60)
        rows = {row["mount_id"]: row for row in self.actor.view.snapshot.overview["mounts"]}
        if "get-manifest" not in self.verbs or f"get-capture {VOLUME} {digest}" not in self.verbs or VOLUME not in rows:
            fail(f"v2 data path: verbs={self.verbs} mounts={list(rows)}")
        print("PASS: collector get-manifest and get-capture of seeded v2 files over the gate; the volume shows as a v1 mount")

        request_id = uuid.uuid4().hex
        ask = Ask()
        self.actor.post(Admit(request_id, "e2e", int(time.time()), 0, ask))
        self.step()
        if ask.value is None or ask.value.error is not None or ask.value.job.protocol != "receipt":
            fail(f"rescan admission over the gate: {ask.value}")
        job = self.until("rescan job finished by receipt", lambda: self.record.last_job if self.record.job is None else None,
                         timeout=240, pause=3)
        receipts = {r.request_id: r.outcome for r in self.record.data.manifest.requests}
        if f"rescan {request_id}" not in self.verbs or request_id not in receipts:
            fail(f"request id not delivered or not in a receipt: verbs={self.verbs[-8:]} receipts={receipts}")
        if job.request_id != request_id or job.state not in ("succeeded", "failed") or job.result_code not in ("OK", "SCAN_FAILED"):
            fail(f"rescan job did not end on its receipt: {job}")
        print(f"PASS: rescan {request_id[:8]} delivered over the gate, the host's receipt says {receipts[request_id]}, "
              f"job {job.state} ({job.result_code})")

    def update_pass(self) -> None:
        desired = self.fleet.desired_agent()
        before = self.host_release()
        self.until("update track activated the desired release",
                   lambda: (self.record.update.state, self.record.update.trial) == ("probation", desired.sha), timeout=240)
        if [v.split()[0] for v in self.verbs if v.split()[0].endswith("-update")] != ["stage-update", "activate-update"] \
                or self.release() != desired.sha or self.record.update.previous != before:
            fail(f"stage/activate through the track: verbs={self.verbs} release={self.release()} update={self.record.update}")
        print(f"PASS: update track staged and activated {desired.sha[:8]} on the canary over the gate (previous {before[:8]})")
        self.seed(last_run=self.run_doc(desired.sha))
        self.until("probation passed", lambda: self.record.update.state == "in_sync", timeout=60)
        view = self.fleet.rollout_view()
        if view["phase"] != "fleet" or view["passed"] != [{"sha": desired.sha, "release_seq": desired.release_seq}]:
            fail(f"rollout after the canary passed: {view}")
        print(f"PASS: probation passed on a run by {desired.sha[:8]}; rollout phase fleet, passed {desired.sha[:8]}")

    def update_fail(self) -> None:
        desired = self.fleet.desired_agent()
        before = self.host_release()
        # The manual rollback from B to A since the last phase is a switch central did not make: A goes on probation.
        self.until("the unexpected switch goes to probation",
                   lambda: (self.record.update.state, self.record.update.trial) == ("probation", before), timeout=60)
        if self.verbs.count("rollback-update " + before) or any(v.startswith(("stage-update", "activate-update")) for v in self.verbs):
            fail(f"central sent an update request while the switched-to release was on probation: {self.verbs}")
        self.seed(last_run=self.run_doc(before))
        self.until("the switched-to release passed probation", lambda: self.record.update.verified == before, timeout=60)
        print(f"PASS: the switch to {before[:8]} that central did not make went through probation before anything else")
        self.until("update track activated the desired release",
                   lambda: (self.record.update.state, self.record.update.trial) == ("probation", desired.sha), timeout=240)
        if self.fleet.rollout_view()["phase"] != "canary" or self.release() != desired.sha:
            fail(f"a new sha did not go through the canary: {self.fleet.rollout_view()} release={self.release()}")
        print(f"PASS: a sha the canary has not passed went to the canary first and was activated ({desired.sha[:8]})")
        self.seed(last_run=self.run_doc(desired.sha, outcome="failed"), error_code="INTERNAL_E2E_FAULT")
        self.until("rolled back", lambda: self.record.update.state == "rolled_back", timeout=180)
        if f"rollback-update {before}" not in self.verbs or f"activate-update {before}" in self.verbs:
            fail(f"probation failure must send rollback-update, never activate-update: {self.verbs}")
        state = json.loads(self.docker("cat", "/var/lib/storage-viz/update/state.json"))
        if self.release() != before or desired.sha not in [entry["sha"] for entry in state["bad"]]:
            fail(f"host did not roll back and mark the release bad: release={self.release()} bad={state['bad']}")
        rollout_file = json.loads((self.args.work / "central-state" / "rollout.json").read_text())
        if rollout_file["phase"] != "halted" or not rollout_file["reason"].startswith(f"{SID}: INTERNAL_E2E_FAULT"):
            fail(f"rollout not halted: {rollout_file}")
        print(f"PASS: probation fault INTERNAL_E2E_FAULT: rollback-update {before[:8]} (no activate-update), host back on "
              f"{before[:8]} with {desired.sha[:8]} marked bad, rollout.json halted")


if __name__ == "__main__":
    main()
