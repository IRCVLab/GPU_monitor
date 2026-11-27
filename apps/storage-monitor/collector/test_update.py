from collector import update
from collector.update import (
    Activate, Attempt, DesiredChanged, Observed, ProbationJudged, Rollback, Sent, Stage, Update, reduce,
)
from tests.machine_check import assert_table_total, run_random_events

OLD, NEW, OTHER = "1" * 40, "2" * 40, "3" * 40


def observed(at, sha, idle=True, job=False, allowed=True, broken=False, attempt=None, host_at=None, cadence=3600,
             permitted=True, manifest=None):
    return Observed(at=at, host_at=at if host_at is None else host_at, release_sha=sha, idle=idle, job_active=job,
                    allowed=allowed, permitted=permitted, broken=broken, attempt=attempt, cadence_sec=cadence,
                    manifest_sha=manifest)


def answer(action, sha, outcome, code=None, at=0):
    return Attempt(action, sha, outcome, code, at)


def sent(at, effect, ok=True, code=None, permitted=True):
    return Sent(at, at, effect, ok, code, permitted)


def activating(at=10):
    track, _ = reduce(Update(desired=NEW), observed(at - 2, OLD))
    track, effects = reduce(track, sent(at, Stage(NEW)))
    assert (track.state, effects) == ("activating", (Activate(NEW),))
    return track


def probation(at=20):
    track, _ = reduce(activating(at - 10), observed(at, NEW, manifest="m" * 64))
    assert (track.state, track.probation_manifest) == ("probation", "m" * 64)
    return track


def rolling_back(at=30):
    track, effects = reduce(probation(at - 10), ProbationJudged(at, at, fault=True, code="AGENT_FAULT"))
    assert (track.state, effects) == ("rolling_back", (Rollback(OLD),))
    return track


def test_update_happy_path_stages_activates_and_leaves_probation_in_sync():
    track, effects = reduce(Update(desired=NEW), observed(1, OLD))
    assert (track.state, effects, track.previous, track.requests) == ("staging", (Stage(NEW),), OLD, 1)
    track, effects = reduce(track, sent(2, Stage(NEW)))
    assert (track.state, effects) == ("activating", (Activate(NEW),))
    track, _ = reduce(track, observed(3, NEW))
    assert (track.state, track.requested_host_unix) == ("probation", 3)
    track, effects = reduce(track, ProbationJudged(4, 4, fault=False))
    assert (track.state, effects, track.previous) == ("in_sync", (), OLD)


def test_staging_waits_for_an_idle_host_without_job_and_rollout_permission():
    for event in (observed(1, OLD, idle=False), observed(1, OLD, job=True), observed(1, OLD, allowed=False)):
        assert reduce(Update(desired=NEW), event) == (Update(desired=NEW), ())
    assert reduce(Update(desired=None), observed(1, OLD)) == (Update(desired=None), ()), "no desired agent: idle"
    assert reduce(Update(desired=NEW), observed(1, NEW)) == (Update(desired=NEW), ())


def test_f1_a_stage_result_that_never_comes_is_staged_again_after_the_resend_window():
    track, _ = reduce(Update(desired=NEW), observed(100, OLD))
    assert reduce(track, observed(100 + update.STAGE_RESEND_SECONDS - 1, OLD)) == (track, ())
    again, effects = reduce(track, observed(100 + update.STAGE_RESEND_SECONDS, OLD))
    assert (effects, again.requests, again.state) == ((Stage(NEW),), 2, "staging")


def test_f2_activate_requests_are_bounded_and_end_in_backoff_with_the_error():
    track = activating(at=100)
    for n in range(2, update.MAX_REQUESTS + 1):
        track, _ = reduce(track, sent(track.requested_host_unix + 1, Activate(NEW), ok=False, code="START_FAILED"))
        assert track.last_error == "START_FAILED"
        track, effects = reduce(track, observed(track.requested_host_unix + update.RESEND_SECONDS + 2, OLD))
        assert (track.state, effects, track.requests) == ("activating", (Activate(NEW),), n)
    track, _ = reduce(track, sent(track.requested_host_unix + 1, Activate(NEW), ok=False, code="START_FAILED"))
    track, effects = reduce(track, observed(track.requested_host_unix + update.RESEND_SECONDS + 2, OLD))
    assert (track.state, track.last_error, track.attempts, effects) == ("backoff", "START_FAILED", 1, ())


def test_f2_unanswered_activations_end_in_backoff_with_no_answer():
    track = activating(at=0)
    for n in range(1, update.MAX_REQUESTS + 1):
        track, effects = reduce(track, observed(n * update.RESEND_SECONDS, OLD))
    assert (track.state, track.last_error) == ("backoff", update.NO_ANSWER)


def test_f2_rollback_requests_are_bounded_and_end_in_rollback_failed():
    track = rolling_back(at=0)
    for n in range(1, update.MAX_REQUESTS + 1):
        track, effects = reduce(track, observed(n * update.RESEND_SECONDS, NEW))
    assert track.state == "rollback_failed" and track.last_error == "AGENT_FAULT"
    for later in range(10):
        assert reduce(track, observed(10_000 + later * 1000, NEW)) == (track, ()), "rollback_failed is terminal"
    assert reduce(track, DesiredChanged(99_999, OTHER))[0].state == "in_sync"


def test_a_result_for_another_sha_is_ignored():
    track, _ = reduce(Update(desired=NEW), observed(1, OLD))
    assert reduce(track, sent(2, Stage(OTHER), ok=False, code="X")) == (track, ())
    assert reduce(activating(), sent(20, Activate(OTHER), ok=False, code="X")) == (activating(), ())
    assert reduce(rolling_back(), sent(40, Rollback(NEW), ok=False, code="X")) == (rolling_back(), ())


def test_a_halt_stops_a_host_before_activation_and_stops_resends():
    track, _ = reduce(Update(desired=NEW), observed(1, OLD))
    stopped, effects = reduce(track, sent(2, Stage(NEW), permitted=False))
    assert (stopped.state, stopped.last_error, effects) == ("in_sync", update.ROLLOUT_STOPPED, ())
    stopped, effects = reduce(activating(), observed(1000, OLD, permitted=False))
    assert (stopped.state, effects) == ("in_sync", ())
    updated, _ = reduce(activating(), observed(1000, NEW, permitted=False))
    assert updated.state == "probation", "a host already on the new sha follows its own probation"


def test_a_probation_fault_sends_rollback_update_never_activate_update():
    for event in (ProbationJudged(30, 30, fault=True, code="CONTRACT_REJECTED"), observed(30, NEW, broken=True),
                  observed(30, NEW, host_at=20 + 3600 + update.PROBATION_GRACE_SECONDS + 1)):
        track, effects = reduce(probation(), event)
        assert (track.state, effects) == ("rolling_back", (Rollback(OLD),)), event
    track, _ = reduce(probation(), observed(30, NEW, host_at=20 + 3600 + update.PROBATION_GRACE_SECONDS))
    assert track.state == "probation", "the run deadline is cadence + 1 h on the host clock"
    track, _ = reduce(rolling_back(), observed(31, OLD))
    assert (track.state, track.last_error) == ("rolled_back", "AGENT_FAULT")
    assert reduce(track, observed(32, OLD))[0].state == "rolled_back", "a rolled-back sha is not staged again"


def test_floor_and_bootstrap_refusals_are_terminal_for_the_sha():
    for code in ("FLOOR", "BOOTSTRAP_TOO_OLD", "BAD_RELEASE", "SELFTEST_BACKOFF"):
        track, effects = reduce(activating(), observed(20, OLD, attempt=answer("activate", NEW, "refused", code, 15)))
        assert (track.state, track.last_error, effects) == ("refused", code, ())
        for later in range(21, 40):
            assert reduce(track, observed(later * 1000, OLD)) == (track, ()), "no retry loop after a refusal"
        assert reduce(track, DesiredChanged(99, OTHER))[0] == Update(desired=OTHER, since_unix=99)


def test_a_failed_self_test_backs_off_15_min_doubling_to_6_h_then_restages():
    track, _ = reduce(activating(), observed(20, OLD, attempt=answer("activate", NEW, "failed", "SELFTEST_FAILED", 15)))
    assert (track.state, track.next_try_unix, track.attempts) == ("backoff", 20 + update.FIRST_BACKOFF_SECONDS, 1)
    assert reduce(track, observed(21, OLD))[0] == track
    track, effects = reduce(track, observed(20 + update.FIRST_BACKOFF_SECONDS, OLD))
    assert (track.state, effects) == ("staging", (Stage(NEW),))
    track, _ = reduce(track, sent(2000, Stage(NEW), ok=False, code="IO_ERROR"))
    assert track.next_try_unix == 2000 + 2 * update.FIRST_BACKOFF_SECONDS
    for _ in range(10):
        track, _ = reduce(update.Update(**{**track.__dict__, "state": "staging"}), sent(5000, Stage(NEW), ok=False))
    assert track.next_try_unix - 5000 == update.MAX_BACKOFF_SECONDS


def test_an_answer_to_an_older_request_is_not_this_requests_answer():
    track = activating(at=100)
    stale = answer("activate", NEW, "failed", "SELFTEST_FAILED", at=99)
    assert reduce(track, observed(101, OLD, idle=False, attempt=stale)) == (track, ())
    other = answer("activate", OTHER, "refused", "FLOOR", at=150)
    assert reduce(track, observed(151, OLD, idle=False, attempt=other)) == (track, ())


def test_busy_answers_and_lost_requests_are_sent_again_only_to_an_idle_host():
    track = activating(at=100)
    busy = answer("activate", NEW, "busy", "BUSY", at=101)
    assert reduce(track, observed(102, OLD, idle=False, attempt=busy)) == (track, ())
    again, effects = reduce(track, observed(103, OLD, attempt=busy))
    assert (effects, again.requested_host_unix, again.requests) == ((Activate(NEW),), 103, 2)
    assert reduce(track, observed(150, OLD)) == (track, ()), "an idle host gets RESEND_SECONDS to answer"
    assert reduce(track, observed(100 + update.RESEND_SECONDS, OLD))[1] == (Activate(NEW),)


def test_a_refused_rollback_ends_refused_and_a_failed_one_is_sent_again():
    track = rolling_back()
    refused, _ = reduce(track, observed(40, NEW, attempt=answer("rollback", OLD, "refused", "FLOOR", 35)))
    assert (refused.state, refused.last_error) == ("refused", "FLOOR")
    failed = answer("rollback", OLD, "failed", "IO_ERROR", 35)
    assert reduce(track, observed(40, NEW, attempt=failed)) == (track, ())
    assert reduce(track, observed(30 + update.RESEND_SECONDS, NEW, attempt=failed))[1] == (Rollback(OLD),)


def test_probation_without_a_previous_release_cannot_roll_back():
    track = Update(desired=NEW, state="probation", previous=None, requested_host_unix=0)
    assert reduce(track, ProbationJudged(1, 1, fault=True))[0].state == "refused"


def test_update_table_total():
    assert_table_total(update.TRANSITIONS, update.STATES, update.EVENT_KINDS)


def test_update_random_events():
    shas = (OLD, NEW, OTHER, None)

    def attempt(rng):
        if rng.random() < 0.4:
            return None
        return Attempt(rng.choice(("activate", "rollback")), rng.choice(shas), rng.choice(
            ("activated", "already_active", "rolled_back", "busy", "refused", "failed")), rng.choice((None, "FLOOR", "SELFTEST_FAILED")),
            rng.randrange(0, 40_000))

    def event(rng, track, index):
        at = index * 1000
        kind = rng.randrange(4)
        if kind == 0:
            return Observed(at, at + rng.randrange(-500, 500), rng.choice(shas), rng.random() < 0.7, rng.random() < 0.2,
                            rng.random() < 0.8, rng.random() < 0.9, rng.random() < 0.1, attempt(rng))
        if kind == 1:
            sha = rng.choice(shas[:3])
            effect = rng.choice((Stage(sha), Activate(sha), Rollback(sha)))
            return Sent(at, at, effect, rng.random() < 0.6, rng.choice((None, "START_FAILED")), rng.random() < 0.9)
        if kind == 2:
            return ProbationJudged(at, at, rng.random() < 0.3)
        return DesiredChanged(at, rng.choice(shas))

    def check(before, event, after, effects):
        assert after.state in update.STATES
        assert after.attempts >= 0 and 0 <= after.requests <= update.MAX_REQUESTS
        assert len(effects) <= 1
        if after.state in update.REQUEST_STATES:
            # F1: a state that sent a request holds a resend deadline, so a lost result never strands it.
            assert after.requested_host_unix is not None and after.requests >= 1, "waiting without a resend deadline"
        if after.state == "rolling_back":
            assert after.previous is not None, "rolling back without a previous release"
        for effect in effects:
            if isinstance(effect, Stage):
                assert after.state == "staging" and effect.sha == after.desired is not None
                if before.state != "staging":
                    assert isinstance(event, Observed) and event.idle and not event.job_active and event.allowed, \
                        "staged on a busy or disallowed host"
            if isinstance(effect, Activate):
                assert after.state == "activating" and effect.sha == after.desired
                assert event.permitted, "activated a host the rollout no longer permits"
            if isinstance(effect, Rollback):
                assert after.state == "rolling_back" and effect.sha == after.previous
        if isinstance(event, Sent) and event.effect not in (Stage(before.desired), Activate(before.desired), Rollback(before.previous)):
            assert (after, effects) == (before, ()), "a result for another sha changed the track"
        if before.state == "probation" and after.state not in ("probation", "in_sync", "rolling_back", "refused"):
            assert isinstance(event, DesiredChanged), "probation left without a verdict or a desired change"
        if before.state in ("refused", "rolled_back", "rollback_failed") and after.state != before.state:
            assert isinstance(event, DesiredChanged), "a terminal answer was retried"

    run_random_events(lambda rng: Update(desired=rng.choice(shas)), event, reduce, check)
