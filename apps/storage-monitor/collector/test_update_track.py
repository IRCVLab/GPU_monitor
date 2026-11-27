"""The update track driven by the actor: what it sends the gate, when, and what it reports to the rollout."""
from __future__ import annotations

from collector import update as updates
from collector.contract import HostRun
from collector.test_bug_harness import FakeUpdater, ManualSpawn, transport_error
from collector.test_contract import encode, manifest_doc, volume_doc
from collector.test_volumes import BA, SID, VA, capture, record, sha, v2_collector

OLD, NEW, NEWER = "1" * 40, "2" * 40, "3" * 40
RUN = "c" * 32


class Desired:
    def __init__(self, sha):
        self.sha = sha

    def read_bundle(self):
        return b"bundle-" + self.sha.encode(), b"sig"


class FakeChannel:
    """A rollout that lets every host stage, with the same slot bookkeeping as fleet.Rollout."""

    def __init__(self, sha=NEW, allow=True):
        self.current = Desired(sha) if sha else None
        self.allow = allow
        self.slots = set()
        self.reports = []

    def desired(self):
        return self.current

    def permits(self, server_id):
        return self.allow

    def acquire(self, server_id):
        if self.allow:
            self.slots.add(server_id)
        return self.allow

    def hold(self, server_id):
        self.slots.add(server_id)

    def release(self, server_id):
        self.slots.discard(server_id)

    def reported(self, server_id, before, after):
        self.reports.append((server_id, before.state, after.state))

    def confirmed(self, server_id, sha):
        self.reports.append((server_id, "confirmed", sha))


def gate_collector(tmp_path, channel=None, release=OLD, **options):
    """A v2 host on ``release`` behind the gate, after one observe with no desired release yet."""
    channel = channel or FakeChannel()
    desired, channel.current = channel.current, None
    collector, host = v2_collector(tmp_path, channel=channel, **options)
    collector.host.agents[SID] = FakeUpdater(release)
    channel.current = desired
    return collector, host, channel


def track(collector):
    return record(collector).update


def calls(collector):
    return [(verb, sha) for _, verb, sha in collector.host.update_calls]


def written_by(doc, agent_sha):
    """A manifest document whose agent block names ``agent_sha``, as that release writes it."""
    doc["agent"]["release_sha"] = agent_sha
    return doc


def run_by(collector, agent_sha, *, started, outcome="complete", error=None):
    """The host publishes a manifest whose last run is by ``agent_sha``, with one fresh capture."""
    data = capture(VA, BA, "/a", started + 50)
    collector.host.volume_captures[(VA, sha(data))] = data
    volume = volume_doc(VA, BA, "/a", capture=(sha(data), len(data)), cadence=3600)
    volume["last_attempt"] = {"run_id": RUN, "started_unix": started, "finished_unix": started + 50,
                              "outcome": "failed" if error else "complete", "error_code": error}
    seq = record(collector).data.manifest.seq + 1
    collector.host.manifests[SID] = encode(written_by(manifest_doc(server_id=SID, volumes=[volume], seq=seq, requests=[], last_run={
        "run_id": RUN, "trigger": "timer", "plan": "full", "started_unix": started, "finished_unix": started + 50,
        "outcome": outcome, "agent_sha": agent_sha}), agent_sha))


def to_probation(collector):
    collector.tick()
    assert calls(collector) == [("stage-update", NEW), ("activate-update", NEW)]
    collector.tick()
    assert track(collector).state == "probation"


def test_a_gate_host_behind_the_desired_release_stages_activates_and_passes_probation(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    to_probation(collector)
    assert collector.host.agents[SID].staged[NEW] == (b"bundle-" + NEW.encode(), b"sig")
    assert track(collector).previous == OLD and channel.slots == set(), "probation holds no staging slot"
    run_by(collector, NEW, started=collector.clock.now + 1)
    collector.tick()
    assert track(collector).state == "in_sync"
    assert (SID, "probation", "in_sync") in channel.reports
    for _ in range(5):
        collector.tick()
    assert len(calls(collector)) == 2, "an in-sync host gets no more requests"


def test_staging_waits_for_an_idle_host_no_job_and_the_rollouts_permission(tmp_path):
    collector, host, channel = gate_collector(tmp_path, channel=FakeChannel(allow=False))
    collector.tick()
    assert calls(collector) == [] and track(collector).state == "in_sync"
    channel.allow = True
    collector.host.runs[SID] = HostRun(present=True, scanning=True, run_id="d" * 32)
    collector.tick()
    assert calls(collector) == [], "staged while the agent runs"
    collector.host.runs[SID] = HostRun(present=True, scanning=False)
    collector.tick()
    assert calls(collector) == [], "staged over an interrupted run (run.json left)"
    collector.host.runs[SID] = HostRun()
    collector.tick()
    assert calls(collector)[0] == ("stage-update", NEW)


def test_no_desired_agent_and_s2_hosts_leave_the_track_idle(tmp_path):
    collector, host, channel = gate_collector(tmp_path, channel=FakeChannel(sha=None))
    for _ in range(3):
        collector.tick()
    assert calls(collector) == [] and track(collector) == updates.Update(since_unix=None)
    channel.current = Desired(NEW)
    collector.host.delivers_request_ids = False  # the host answers over S2
    for _ in range(3):
        collector.tick()
    assert calls(collector) == [] and track(collector).desired == NEW and track(collector).state == "in_sync"


def test_update_requests_run_on_the_transfer_worker_under_the_fleet_semaphore(tmp_path):
    spawn = ManualSpawn()
    collector, host, channel = gate_collector(tmp_path, spawn=spawn)
    spawn.run()
    collector.settle()
    collector.tick()
    assert [name for name, _ in spawn.pending] == [f"storage-viz-update-{SID}"]
    free = []

    def stage(server, sha, bundle, signature):
        semaphore = collector.actor().transfers
        while semaphore.acquire(blocking=False):
            free.append(semaphore)
        for held in free:
            held.release()

    collector.host.stage_update = stage
    spawn.run()
    assert len(free) == 1, "stage-update did not hold one of the fleet's two transfer slots"


def test_a_probation_fault_rolls_back_with_rollback_update_and_never_activate_update(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    to_probation(collector)
    run_by(collector, NEW, started=collector.clock.now + 1, outcome="failed", error="INTERNAL_CRASH")
    collector.tick()
    assert calls(collector)[-1] == ("rollback-update", OLD)
    assert ("activate-update", OLD) not in calls(collector)
    collector.tick()
    assert track(collector).state == "rolled_back" and track(collector).last_error == "INTERNAL_CRASH"
    assert NEW in collector.host.agents[SID].bad, "the host marks the release bad only on rollback-update"
    assert (SID, "rolling_back", "rolled_back") in channel.reports
    for _ in range(5):
        collector.tick()
    assert calls(collector).count(("stage-update", NEW)) == 1, "a rolled-back release is staged again"


def test_a_rejected_manifest_or_capture_in_probation_is_contract_rejected(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    to_probation(collector)
    collector.host.manifests[SID] = b'{"schema": "storage-viz.manifest/2", "seq": 99, "phase": "publishing"}'
    collector.tick()
    assert (track(collector).state, track(collector).last_error) in {("rolling_back", "CONTRACT_REJECTED"), ("rolled_back", "CONTRACT_REJECTED")}
    assert ("rollback-update", OLD) in calls(collector)


def test_a_capture_rejected_from_the_new_release_fails_probation(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    to_probation(collector)
    data = capture(VA, "b-0000000000000099", "/a", collector.clock.now)  # its binding differs from the manifest's
    collector.host.volume_captures[(VA, sha(data))] = data
    volume = volume_doc(VA, BA, "/a", capture=(sha(data), len(data)), cadence=3600)
    started = collector.clock.now + 1
    collector.host.manifests[SID] = encode(written_by(manifest_doc(server_id=SID, volumes=[volume], seq=record(collector).data.manifest.seq + 1, requests=[],
                                                        last_run={"run_id": RUN, "trigger": "timer", "plan": "full",
                                                                  "started_unix": started, "finished_unix": started + 1,
                                                                  "outcome": "complete", "agent_sha": NEW}), NEW))
    collector.tick()
    assert track(collector).last_error == "CONTRACT_REJECTED" and ("rollback-update", OLD) in calls(collector)


def test_the_gate_reporting_the_agent_broken_rolls_back(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    to_probation(collector)
    collector.host.agents[SID].broken = True
    collector.tick()
    assert (calls(collector)[-1], track(collector).last_error) == (("rollback-update", OLD), "AGENT_BROKEN")


def test_no_run_within_cadence_plus_an_hour_rolls_back(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    to_probation(collector)
    collector.tick(3600 + 3000)
    assert track(collector).state == "probation", "the volumes' 3600 s cadence plus one hour has not passed"
    run_by(collector, NEW, started=collector.clock.now, outcome="nothing_due")
    collector.tick(1000)
    assert track(collector).last_error == "PROBATION_NO_RUN" and calls(collector)[-1] == ("rollback-update", OLD), \
        "a nothing_due run is not a real run"


def test_a_run_by_the_old_release_or_from_before_the_activation_decides_nothing(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    to_probation(collector)
    run_by(collector, OLD, started=collector.clock.now + 1)
    collector.tick()
    run_by(collector, NEW, started=collector.clock.now - 500)
    collector.tick()
    assert track(collector).state == "probation"


def test_floor_and_bootstrap_refusals_end_refused_with_no_retry(tmp_path):
    for code in ("FLOOR", "BOOTSTRAP_TOO_OLD"):
        collector, host, channel = gate_collector(tmp_path / code)
        collector.host.agents[SID].answers[NEW] = ("refused", code)
        for _ in range(30):
            collector.tick(600)
        assert (track(collector).state, track(collector).last_error) == ("refused", code)
        assert calls(collector) == [("stage-update", NEW), ("activate-update", NEW)], "a terminal answer was retried"
        assert (SID, "activating", "refused") in channel.reports
        channel.current = Desired(NEWER)
        collector.tick()
        assert ("stage-update", NEWER) in calls(collector), "a new desired release starts over"


def test_a_failed_self_test_backs_off_then_the_hosts_own_backoff_is_refused(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    collector.host.agents[SID].answers[NEW] = ("failed", "SELFTEST_FAILED")
    collector.tick()
    collector.tick()
    assert (track(collector).state, track(collector).attempts) == ("backoff", 1)
    collector.tick(updates.FIRST_BACKOFF_SECONDS - 120)
    assert len(calls(collector)) == 2, "restaged inside the back-off"
    collector.host.agents[SID].answers[NEW] = ("refused", "SELFTEST_BACKOFF")
    collector.tick(200)
    collector.tick()
    assert calls(collector)[2:] == [("stage-update", NEW), ("activate-update", NEW)]
    assert (track(collector).state, track(collector).last_error) == ("refused", "SELFTEST_BACKOFF")


def test_a_busy_updater_gets_the_request_again_once_the_host_is_idle(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    collector.host.agents[SID].answers[NEW] = ("busy", "BUSY")
    collector.tick()
    collector.host.runs[SID] = HostRun(present=True, scanning=True, run_id="d" * 32)
    collector.tick()
    assert calls(collector).count(("activate-update", NEW)) == 1
    del collector.host.agents[SID].answers[NEW]
    collector.host.runs[SID] = HostRun()
    collector.tick()
    collector.tick()
    assert calls(collector).count(("activate-update", NEW)) == 2 and track(collector).state == "probation"


def test_a_failed_stage_backs_off_and_frees_the_staging_slot(tmp_path):
    collector, host, channel = gate_collector(tmp_path)
    collector.host.update_errors[(SID, "stage-update")] = transport_error("UNREACHABLE")
    collector.tick()
    assert (track(collector).state, track(collector).last_error) == ("backoff", "UNREACHABLE")
    assert channel.slots == set() and record(collector).link.state == "unreachable"


def test_a_stage_whose_result_never_comes_is_staged_again_and_a_restart_mid_activation_keeps_the_slot(tmp_path):
    spawn = ManualSpawn()
    collector, host, channel = gate_collector(tmp_path, spawn=spawn)
    spawn.run()
    collector.settle()
    collector.tick()
    assert track(collector).state == "staging"
    spawn.pending.clear()  # the stage died with the process: no result will come
    collector.restart()
    assert (track(collector).state, channel.slots) == ("staging", {SID})
    collector.tick(updates.STAGE_RESEND_SECONDS - 120)
    assert spawn.pending == [], "staged again inside the resend window"
    collector.tick(200)
    assert [name for name, _ in spawn.pending] == [f"storage-viz-update-{SID}"], "the lost stage was never sent again"
    assert track(collector).requests == 2
    spawn.run()
    collector.settle()
    assert track(collector).state == "activating"
    channel.slots.clear()
    collector.restart()
    assert channel.slots == {SID} and track(collector).state == "activating"
