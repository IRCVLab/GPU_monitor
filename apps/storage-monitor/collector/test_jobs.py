from dataclasses import replace

from collector import jobs
from collector.jobs import (
    Dispatch, Job, RescanReplied, Restarted, Tick, Traced, admit, reduce,
)
from tests.machine_check import assert_table_total, run_random_events

def requested():
    """A v2 job whose rescan <request_id> the host answered ``received``."""
    job, effects = reduce(admit("q" * 32, "operator", 0), Traced(at=5))
    assert (job.state, effects) == ("dispatching", (Dispatch(),))
    job, _ = reduce(job, RescanReplied(at=6, outcome="received"))
    assert job.state == "pending"
    return job


def test_receipt_complete_succeeds_the_job_once_its_captures_are_held():
    job, _ = reduce(requested(), Traced(at=10, running=True))
    assert job.state == "running"
    job, _ = reduce(job, Traced(at=20, receipt="complete", captures="fetching"))
    assert job.state == "collecting"
    job, _ = reduce(job, Traced(at=30, receipt="complete", captures="current"))
    assert (job.state, job.result_code, job.finished_unix) == ("succeeded", "OK", 30)


def test_receipt_interrupted_fails_the_job_with_a_code():
    job, _ = reduce(requested(), Traced(at=10, running=True))
    job, _ = reduce(job, Traced(at=20, receipt="interrupted"))
    assert (job.state, job.result_code) == ("failed", "SCAN_INTERRUPTED")
    job, _ = reduce(requested(), Traced(at=20, receipt="failed"))
    assert (job.state, job.result_code) == ("failed", "SCAN_FAILED")


def test_request_id_in_run_json_keeps_the_job_running():
    job, _ = reduce(requested(), Traced(at=10, running=True))
    for at in range(20, 2000, 60):
        job, effects = reduce(job, Traced(at=at, running=True))
        assert (job.state, effects) == ("running", ())
    job, effects = reduce(job, Traced(at=3000))
    assert (job.state, effects) == ("running", ()), "run.json gone before the receipt arrived is not an ending"


def test_c4_scheduled_scan_while_the_request_waits_keeps_it_pending():
    job = requested()
    for at in range(10, 600, 5):
        job, effects = reduce(job, Traced(at=at, pending=True))
        assert (job.state, effects) == ("pending", ()), f"C4 scheduled scan moved the request to {job.state}"
    job, _ = reduce(job, Traced(at=700, running=True))
    assert job.state == "running"


def test_a_delivered_request_is_never_sent_again():
    # M5a review F2: an id in the mailbox read as idle, so three sends went out within seconds.
    job = requested()
    for at in range(10, 4000, 5):
        job, effects = reduce(job, Traced(at=at, pending=True))
        assert (job.state, effects) == ("pending", ()), "a delivered request was sent again"


def test_a_request_with_no_trace_is_resent_once_per_idle_interval():
    job = requested()
    sent = job.last_dispatch_unix
    job, effects = reduce(job, Traced(at=sent + jobs.RESEND_INTERVAL_SECONDS - 1))
    assert effects == ()
    job, effects = reduce(job, Traced(at=sent + jobs.RESEND_INTERVAL_SECONDS))
    assert (effects, job.dispatches, job.request_id) == ((Dispatch(),), 2, "q" * 32)


def test_unknown_receipts_are_ignored():
    for state in ("dispatching", "pending", "running", "reconciling", "collecting"):
        job = Job("q" * 32, "op", 0, 0, state=state, dispatches=1)
        for trace in ({}, {"pending": True}, {"running": True}):
            unknown = reduce(job, Traced(at=10, receipt="vanished", **trace))
            assert unknown == reduce(job, Traced(at=10, **trace)), f"an unknown receipt changed a {state} job"
            assert unknown[0].state not in jobs.TERMINAL


def test_ambiguous_reply_reconciles_by_observation_and_resends_the_same_id():
    job, _ = reduce(admit("q" * 32, "operator", 0), Traced(at=5))
    job, _ = reduce(job, RescanReplied(at=6, outcome="ambiguous", code="TIMEOUT"))
    assert job.state == "reconciling"
    job, effects = reduce(job, Traced(at=10))
    assert (job.state, effects, job.dispatches) == ("reconciling", (), 1), "resent before the idle interval"
    job, effects = reduce(job, Traced(at=5 + jobs.RESEND_INTERVAL_SECONDS))
    assert (job.state, effects, job.dispatches) == ("reconciling", (Dispatch(),), 2)
    job, _ = reduce(job, Traced(at=200, receipt="partial"))
    assert (job.state, job.result_code) == ("succeeded", "OK")


def test_rejected_request_fails_and_reconcile_deadline_is_unconfirmed():
    job, _ = reduce(admit("q" * 32, "operator", 0), Traced(at=5))
    failed, _ = reduce(job, RescanReplied(at=6, outcome="rejected", code="TOO_MANY_PENDING"))
    assert (failed.state, failed.result_code) == ("failed", "TOO_MANY_PENDING")
    job, _ = reduce(job, RescanReplied(at=6, outcome="ambiguous"))
    job, _ = reduce(job, Tick(at=6 + jobs.RECONCILE_DEADLINE_SECONDS))
    assert (job.state, job.result_code) == ("unconfirmed", "NO_TRACE")


def test_receipt_with_a_rejected_capture_fails_the_job():
    job, _ = reduce(requested(), Traced(at=10, receipt="complete", captures="rejected"))
    assert (job.state, job.result_code) == ("failed", "CAPTURE_REJECTED")


def test_receipt_resends_are_bounded():
    job, starts = requested(), 1
    for at in range(10, 1000, 5):
        job, effects = reduce(job, Traced(at=at))
        starts += len(effects)
        if job.state in jobs.TERMINAL:
            break
    assert starts == jobs.MAX_DISPATCHES
    assert (job.state, job.result_code) == ("failed", "RESCAN_NOT_STARTED")
    job, _ = reduce(admit("q" * 32, "operator", 0), Traced(at=5))
    job, _ = reduce(job, RescanReplied(at=6, outcome="ambiguous"))
    for at in range(10, 1000, 5):
        job, _ = reduce(job, Traced(at=at))
        if job.state in jobs.TERMINAL:
            break
    assert (job.state, job.result_code, job.dispatches) == ("unconfirmed", "NO_TRACE", jobs.MAX_DISPATCHES)


def test_a_receipt_job_ends_at_the_deadline_from_admission():
    limit = jobs.job_deadline_seconds(jobs.MIN_RUN_DEADLINE_SECONDS)
    job, _ = reduce(requested(), Traced(at=limit - 60, running=True))
    assert reduce(job, Tick(at=limit - 1))[0].state == "running"
    assert reduce(job, Tick(at=limit))[0].result_code == "RESCAN_TIMEOUT"


def test_job_table_total():
    assert_table_total(jobs.TRANSITIONS, jobs.STATES, jobs.EVENT_KINDS)


def test_job_random_events():
    def event(rng, job, index):
        at = index * 1800
        kind = rng.randrange(2, 6)
        if kind == 2:
            return Tick(at, jobs.run_deadline_seconds(rng.choice((None, 600, 7200))))
        if kind == 3:
            return Traced(at, pending=rng.random() < 0.3, running=rng.random() < 0.3,
                          receipt=rng.choice((None, None, "complete", "partial", "failed", "interrupted", "nothing_due", "bogus")),
                          captures=rng.choice(("current", "fetching", "rejected")))
        if kind == 4:
            return RescanReplied(at, rng.choice(("received", "already_received", "rejected", "ambiguous")), "CODE")
        return Restarted(at)

    def check(before, event, after, effects):
        assert after.state in jobs.STATES
        if before.state in jobs.TERMINAL:
            assert after == before, "a terminal job changed"
        if after.state == "succeeded" and before.state != "succeeded":
            assert isinstance(event, Traced) and event.receipt in ("complete", "partial"), "I24 succeeded without a receipt"
            assert event.captures == "current", "succeeded before its captures were held"
        if isinstance(event, Traced) and event.receipt not in jobs.RECEIPT_SUCCEEDS | set(jobs.RECEIPT_FAILS):
            assert reduce(before, replace(event, receipt=None)) == (after, effects), "an unknown receipt changed the outcome"
        if effects:
            assert after.last_dispatch_unix == event.at
            assert before.last_dispatch_unix is None or event.at - before.last_dispatch_unix >= jobs.RESEND_INTERVAL_SECONDS, \
                "a receipt job resent within the idle interval"
            assert not (isinstance(event, Traced) and event.pending and before.dispatches), "a delivered request was sent again"
        if effects:
            assert effects == (Dispatch(),)
            assert after.state == "dispatching" or after.state in ("pending", "reconciling")
            assert after.dispatches == before.dispatches + 1, "dispatched twice for one attempt"
        assert after.request_id == before.request_id, "a second request id for one admitted job"
        assert after.dispatches <= jobs.MAX_DISPATCHES, "the scan was started without limit"
        if isinstance(event, Tick) and event.at - after.requested_unix >= jobs.job_deadline_seconds(event.run_deadline_seconds):
            assert after.state in jobs.TERMINAL, "a job outlived its deadline from admission"

    run_random_events(lambda rng: admit("q" * 32, "op", 0), event, reduce, check)
