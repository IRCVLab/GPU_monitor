from collector import update
from collector.update import (
    Activate, ActivateDone, DesiredChanged, Observed, ProbationJudged, Stage, StageDone, Tick, Update, reduce,
)
from tests.machine_check import assert_table_total, run_random_events

OLD, NEW = "1" * 40, "2" * 40


def observed(at, sha, idle=True, job=False, allowed=True):
    return Observed(at=at, release_sha=sha, idle=idle, job_active=job, allowed=allowed)


def test_update_happy_path_stages_activates_and_clears_probation():
    track, effects = reduce(Update(desired=NEW), observed(1, OLD))
    assert (track.state, effects) == ("staging", (Stage(NEW),))
    track, effects = reduce(track, StageDone(2, ok=True))
    assert (track.state, effects) == ("activating", (Activate(NEW),))
    track, _ = reduce(track, observed(3, NEW))
    assert track.state == "probation"
    track, _ = reduce(track, ProbationJudged(4, fault=False))
    assert (track.state, track.previous) == ("in_sync", OLD)


def test_update_waits_for_idle_host_without_job_and_rollout_permission():
    for event in (observed(1, OLD, idle=False), observed(1, OLD, job=True), observed(1, OLD, allowed=False)):
        assert reduce(Update(desired=NEW), event) == (Update(desired=NEW), ())


def test_update_fault_in_probation_rolls_back_to_previous():
    track = Update(desired=NEW, state="probation", previous=OLD, since_unix=0)
    track, effects = reduce(track, ProbationJudged(5, fault=True, code="CONTRACT_REJECTED"))
    assert (track.state, effects) == ("rolling_back", (Activate(OLD),))
    track, _ = reduce(track, observed(6, OLD))
    assert track.state == "rolled_back"
    assert reduce(track, DesiredChanged(7, "3" * 40))[0].state == "in_sync"


def test_update_failed_stage_backs_off_with_doubling_delay():
    track, _ = reduce(Update(desired=NEW, state="staging"), StageDone(0, ok=False, code="IO"))
    assert (track.state, track.next_try_unix) == ("backoff", update.FIRST_BACKOFF_SECONDS)
    assert reduce(track, Tick(10))[0] == track
    track, effects = reduce(track, Tick(update.FIRST_BACKOFF_SECONDS))
    assert (track.state, effects) == ("staging", (Stage(NEW),))
    track, _ = reduce(track, StageDone(1, ok=False))
    assert track.next_try_unix == 1 + 2 * update.FIRST_BACKOFF_SECONDS


def test_update_refusal_waits_for_a_new_desired_release():
    track, _ = reduce(Update(desired=NEW, state="activating"), ActivateDone(1, "refused", "FLOOR"))
    assert track.state == "refused"
    assert reduce(track, observed(2, OLD)) == (track, ())


def test_update_table_total():
    assert_table_total(update.TRANSITIONS, update.STATES, update.EVENT_KINDS)


def test_update_random_events():
    shas = (OLD, NEW, "3" * 40, None)

    def event(rng, track, index):
        kind = rng.randrange(6)
        if kind == 0:
            return Observed(index, rng.choice(shas), rng.random() < 0.7, rng.random() < 0.2, rng.random() < 0.8)
        if kind == 1:
            return StageDone(index, rng.random() < 0.6)
        if kind == 2:
            return ActivateDone(index, rng.choice(("ok", "busy", "failed", "refused")))
        if kind == 3:
            return ProbationJudged(index, rng.random() < 0.3)
        if kind == 4:
            return DesiredChanged(index, rng.choice(shas))
        return Tick(index * 1000)

    def check(before, event, after, effects):
        assert after.state in update.STATES
        assert after.attempts >= 0
        if after.state == "rolling_back":
            assert after.previous is not None, "rolling back without a previous release"
        for effect in effects:
            assert after.desired is not None or isinstance(effect, Activate)
            if isinstance(effect, Stage):
                assert after.state == "staging" and effect.sha == after.desired
            if isinstance(effect, Activate) and after.state != "rolling_back":
                assert effect.sha == after.desired
        if isinstance(event, Observed) and before.state == "in_sync" and effects:
            assert event.idle and not event.job_active and event.allowed, "staged on a busy or disallowed host"

    run_random_events(lambda rng: Update(desired=rng.choice(shas)), event, reduce, check)
