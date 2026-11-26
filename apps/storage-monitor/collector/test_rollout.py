"""The fleet rollout (design §3.4, I23): the pure reducer, rollout.json, and the fleet's channel driving real actors."""
from __future__ import annotations

import hashlib
import json

import pytest

from collector import rollout
from collector.fleet import Rollout, load_desired_agent
from collector.rollout import (
    DesiredChanged, HostFailed, HostInSync, HostRolledBack, Passed, RolloutState, allows, host_event, reduce,
)
from collector.test_bug_harness import Collector, FakeUpdater, make_server
from collector.test_contract import encode, manifest_doc, volume_doc
from collector.test_volumes import BA, VA, capture, sha
from collector.update import Update
from tests.machine_check import assert_table_total, run_random_events

A, B, C, D = "a" * 40, "b" * 40, "c" * 40, "d" * 40
CANARY, OTHER, THIRD = "s1", "s2", "s3"


def state(**fields):
    return RolloutState(**{"desired_sha": B, "release_seq": 2, "canary": CANARY, **fields})


# ------------------------------------------------------------------ reducer


def test_a_new_sha_goes_to_the_canary_and_a_passed_sha_straight_to_the_fleet():
    fresh = reduce(state(phase="fleet", passed=(Passed(A, 1),)), DesiredChanged(C, 3))
    assert (fresh.phase, fresh.desired_sha, fresh.release_seq) == ("canary", C, 3)
    rolled_back = reduce(fresh, DesiredChanged(A, 1))
    assert rolled_back.phase == "fleet", "a dashboard rollback to a passed sha needs no canary"


def test_the_canary_in_sync_passes_the_sha_and_opens_the_fleet():
    passed = reduce(state(), HostInSync(CANARY, B))
    assert (passed.phase, passed.passed) == ("fleet", (Passed(B, 2),))
    assert reduce(state(), HostInSync(OTHER, B)) == state(), "only the canary passes a sha"
    assert reduce(state(), HostInSync(CANARY, A)) == state(), "a stale sha passes nothing"


def test_passed_keeps_the_last_20():
    current = state(passed=tuple(Passed(f"{n:040x}", n) for n in range(20)))
    current = reduce(current, HostInSync(CANARY, B))
    assert len(current.passed) == 20 and current.passed[-1] == Passed(B, 2) and current.passed[0].release_seq == 1


def test_a_failed_canary_or_a_fleet_rollback_halts_until_the_desired_sha_changes():
    for phase, event in (("canary", HostFailed(CANARY, B, "FLOOR")), ("canary", HostRolledBack(CANARY, B, "INTERNAL_X")),
                         ("fleet", HostRolledBack(OTHER, B, "CONTRACT_REJECTED"))):
        halted = reduce(state(phase=phase), event)
        assert halted.phase == "halted" and event.host in halted.reason
        for later in (HostInSync(CANARY, B), HostFailed(OTHER, B), HostRolledBack(OTHER, B), DesiredChanged(B, 2)):
            assert reduce(halted, later) == halted, "halted left without a desired change"
        assert reduce(halted, DesiredChanged(C, 3)).phase == "canary"
    assert reduce(state(phase="fleet"), HostFailed(OTHER, B, "FLOOR")).phase == "fleet", "one refusing host does not halt"
    assert reduce(state(), HostFailed(OTHER, B, "FLOOR")).phase == "canary"


def test_allows_the_canary_alone_then_everyone_and_nobody_while_halted():
    assert allows(state(), CANARY) and not allows(state(), OTHER)
    assert allows(state(phase="fleet"), OTHER)
    assert not allows(state(phase="halted"), CANARY)
    assert not allows(state(desired_sha=None), CANARY)


def test_rollout_table_total():
    assert_table_total(rollout.TRANSITIONS, rollout.PHASES, rollout.EVENT_KINDS)


def test_rollout_random_events():
    shas, hosts = (A, B, C, D, None), (CANARY, OTHER, THIRD)

    def event(rng, current, index):
        kind = rng.randrange(4)
        sha = rng.choice(shas[:4]) if rng.random() < 0.5 else current.desired_sha or A
        host = rng.choice(hosts)
        if kind == 0:
            return DesiredChanged(rng.choice(shas), index)
        if kind == 1:
            return HostInSync(host, sha)
        if kind == 2:
            return HostFailed(host, sha, "FLOOR")
        return HostRolledBack(host, sha, "INTERNAL_X")

    def step(current, item):
        return reduce(current, item), ()

    def check(before, item, after, effects):
        assert after.phase in rollout.PHASES and len(after.passed) <= rollout.MAX_PASSED
        assert len({entry.sha for entry in after.passed}) == len(after.passed)
        for host in hosts:
            if allows(after, host) and host != after.canary:
                assert after.desired_sha in {entry.sha for entry in after.passed}, \
                    "I23: a non-canary host may stage a sha the canary has not passed"
        if before.phase == "halted" and after.phase != "halted":
            assert isinstance(item, DesiredChanged) and item.sha != before.desired_sha, "halted left without a desired change"
        if after.passed != before.passed:
            assert isinstance(item, HostInSync) and item.host == after.canary

    run_random_events(lambda rng: RolloutState(desired_sha=rng.choice(shas), canary=CANARY), event, step, check)


def test_track_changes_map_to_rollout_events():
    probation = Update(desired=B, state="probation", previous=A)
    assert host_event(OTHER, probation, Update(desired=B, state="in_sync")) == HostInSync(OTHER, B)
    assert host_event(OTHER, probation, Update(desired=B, state="rolling_back", last_error="AGENT_BROKEN")) == \
        HostRolledBack(OTHER, B, "AGENT_BROKEN"), "a failed probation halts at once, not after the host answers"
    assert host_event(OTHER, Update(desired=B, state="activating"), Update(desired=B, state="refused", last_error="FLOOR")) == \
        HostFailed(OTHER, B, "FLOOR")
    assert host_event(OTHER, Update(desired=B, state="staging", attempts=2), Update(desired=B, state="backoff", attempts=3)).host == OTHER
    assert host_event(OTHER, Update(desired=B, state="staging", attempts=0), Update(desired=B, state="backoff", attempts=1)) is None
    assert host_event(OTHER, Update(desired=A, state="probation"), Update(desired=B, state="in_sync")) is None, "a desired change is no verdict"
    assert host_event(OTHER, Update(desired=B, state="rolling_back"), Update(desired=B, state="rolled_back")) is None


def test_rollout_json_round_trips_and_refuses_other_shapes():
    current = state(phase="halted", reason="s1: FLOOR", passed=(Passed(A, 1),))
    assert rollout.parse(rollout.encode(current)) == current
    raw = json.loads(rollout.encode(current))
    for broken in ({**raw, "phase": "paused"}, {**raw, "extra": 1}, {**raw, "desired_sha": "x"},
                   {**raw, "passed": [{"sha": A}]}, {**raw, "schema": "storage-viz.rollout/2"}):
        with pytest.raises(ValueError):
            rollout.parse(json.dumps(broken).encode())


# ------------------------------------------------------------------ fleet.Rollout: desired release and staging slots


def write_agent(directory, source_sha, seq=7, bundle=b"bundle-bytes"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "storage-viz-agent.tar.gz").write_bytes(bundle)
    (directory / "storage-viz-agent.tar.gz.sig").write_bytes(b"sig")
    metadata = {"application": "storage-viz-agent", "source_sha": source_sha, "release_seq": seq, "rollback_floor_seq": 0,
                "bootstrap_version": 1, "archive": "storage-viz-agent.tar.gz", "sha256": hashlib.sha256(bundle).hexdigest(),
                "size": len(bundle)}
    (directory / "storage-viz-agent.json").write_text(json.dumps(metadata))
    return directory / "storage-viz-agent.json"


def servers(*ids, canary=None):
    import dataclasses
    return [dataclasses.replace(make_server(sid, order=index), canary=sid == canary) for index, sid in enumerate(ids, 1)]


def test_desired_agent_comes_from_the_release_metadata_and_its_absence_means_none(tmp_path):
    path = write_agent(tmp_path / "agent", B)
    desired = load_desired_agent(path)
    assert (desired.sha, desired.release_seq) == (B, 7)
    assert desired.read_bundle() == (b"bundle-bytes", b"sig")
    (tmp_path / "agent" / "storage-viz-agent.tar.gz").write_bytes(b"tampered!!!!")
    with pytest.raises(ValueError):
        desired.read_bundle()
    assert load_desired_agent(tmp_path / "missing.json") is None
    path.write_text('{"application": "storage-viz-agent", "source_sha": "nope"}')
    assert load_desired_agent(path) is None


def test_a_metadata_change_is_desired_changed_and_rollout_json_persists_it(tmp_path):
    path = tmp_path / "agent" / "storage-viz-agent.json"
    channel = Rollout(tmp_path / "state", servers(CANARY, OTHER), path)
    assert channel.desired() is None and channel.view().desired_sha is None
    write_agent(tmp_path / "agent", B)
    assert channel.desired().sha == B
    assert (channel.view().phase, channel.view().canary) == ("canary", CANARY), "no flag: the lowest order is the canary"
    assert rollout.parse((tmp_path / "state" / "rollout.json").read_bytes()).desired_sha == B
    channel.reported(CANARY, Update(desired=B, state="probation"), Update(desired=B, state="in_sync"))
    write_agent(tmp_path / "agent", C, seq=8, bundle=b"other-bundle")
    assert (channel.view().desired_sha, channel.view().phase) == (C, "canary")
    write_agent(tmp_path / "agent", B)  # dashboard rollback to the release that passed
    assert channel.view().phase == "fleet"


def test_staging_slots_canary_alone_then_two_at_a_time_and_none_while_halted(tmp_path):
    write_agent(tmp_path / "agent", B)
    channel = Rollout(tmp_path / "state", servers(CANARY, OTHER, THIRD, "s4", canary=THIRD), tmp_path / "agent" / "storage-viz-agent.json")
    assert not channel.acquire(CANARY) and channel.acquire(THIRD), "the inventory's canary flag decides"
    channel.release(THIRD)
    channel.reported(THIRD, Update(desired=B, state="probation"), Update(desired=B, state="in_sync"))
    assert channel.acquire(CANARY) and channel.acquire(OTHER) and channel.acquire(CANARY)
    assert not channel.acquire("s4"), "at most two hosts stage at once in the fleet phase"
    channel.release(OTHER)
    assert channel.acquire("s4")
    channel.reported(OTHER, Update(desired=B, state="probation"), Update(desired=B, state="rolling_back", last_error="X"))
    channel.release(CANARY)
    channel.release("s4")
    assert channel.view().phase == "halted" and not channel.acquire(CANARY)


def test_a_restart_keeps_a_halted_rollout_halted(tmp_path):
    path = write_agent(tmp_path / "agent", B)
    channel = Rollout(tmp_path / "state", servers(CANARY, OTHER), path)
    channel.reported(CANARY, Update(desired=B, state="activating"), Update(desired=B, state="refused", last_error="FLOOR"))
    assert channel.view().phase == "halted"
    again = Rollout(tmp_path / "state", servers(CANARY, OTHER), path)
    assert (again.view().phase, again.view().reason) == ("halted", "s1: FLOOR")
    (tmp_path / "state" / "rollout.json").write_text("{broken")
    assert Rollout(tmp_path / "state", servers(CANARY, OTHER), path).view().phase == "canary", \
        "a lost rollout.json gates the desired sha through the canary again"


# ------------------------------------------------------------------ the fleet driving real actors


def fleet(tmp_path, ids):
    """Gate hosts on release A; the dashboard release then carries agent B."""
    collector = Collector(tmp_path, *ids, publish=False, desired_path=tmp_path / "agent" / "storage-viz-agent.json")
    finished = collector.clock.now - 600
    for sid in ids:
        data = capture(VA, BA, "/a", finished)
        collector.host.volume_captures[(VA, sha(data))] = data
        collector.host.manifests[sid] = encode(manifest_doc(server_id=sid, seq=1, requests=[], volumes=[
            volume_doc(VA, BA, "/a", capture=(sha(data), len(data)), cadence=3600)]))
        collector.host.agents[sid] = FakeUpdater(A)
    write_agent(tmp_path / "agent", B)
    return collector


def staged(collector):
    return [sid for sid, verb, _ in collector.host.update_calls if verb == "stage-update"]


def pass_probation(collector, sid):
    data = capture(VA, BA, "/a", collector.clock.now + 50)
    collector.host.volume_captures[(VA, sha(data))] = data
    volume = volume_doc(VA, BA, "/a", capture=(sha(data), len(data)), cadence=3600)
    volume["last_attempt"] = None
    collector.host.manifests[sid] = encode(manifest_doc(server_id=sid, seq=2, requests=[], volumes=[volume], last_run={
        "run_id": "c" * 32, "trigger": "timer", "plan": "full", "started_unix": collector.clock.now + 1,
        "finished_unix": collector.clock.now + 50, "outcome": "complete", "agent_sha": B}))


def test_i23_no_host_but_the_canary_stages_until_the_canary_passes_then_two_at_a_time(tmp_path):
    ids = ("s1", "s2", "s3", "s4")
    collector = fleet(tmp_path, ids)
    for _ in range(3):
        collector.tick()
    assert staged(collector) == ["s1"], "a non-canary host staged before the canary passed"
    assert collector.fleet.rollout_view()["phase"] == "canary"
    for sid in ("s2", "s3", "s4"):
        collector.host.agents[sid].answers[B] = ("busy", "BUSY")  # keep them activating: each holds a slot
    pass_probation(collector, "s1")
    collector.tick()
    assert collector.fleet.rollout_view()["phase"] == "fleet"
    assert collector.fleet.rollout_view()["passed"] == [{"sha": B, "release_seq": 7}]
    collector.tick()
    assert sorted(staged(collector)) == ["s1", "s2", "s3"], "more than two hosts staged at once"
    for sid in ("s2", "s3", "s4"):
        del collector.host.agents[sid].answers[B]
    for _ in range(3):
        collector.tick(400)
    assert sorted(staged(collector)) == ["s1", "s2", "s3", "s4"]


def test_a_canary_failing_probation_halts_the_rollout_and_no_other_host_stages(tmp_path):
    ids = ("s1", "s2", "s3")
    collector = fleet(tmp_path, ids)
    collector.tick()
    collector.tick()
    collector.host.agents["s1"].broken = True
    collector.tick()
    view = collector.fleet.rollout_view()
    assert (view["phase"], view["reason"]) == ("halted", "s1: AGENT_BROKEN")
    assert ("s1", "rollback-update", A) in collector.host.update_calls
    for _ in range(5):
        collector.tick(600)
    assert staged(collector) == ["s1"]
    assert json.loads((collector.state_dir / "rollout.json").read_text())["phase"] == "halted"
