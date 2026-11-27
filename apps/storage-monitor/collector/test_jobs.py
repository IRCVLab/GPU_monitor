from dataclasses import replace

from collector import jobs
from collector.jobs import (
    Dispatch, DispatchReturned, Job, Observed, RescanReplied, Restarted, Scan, Tick, Traced, admit, reduce,
)
from tests.machine_check import assert_table_total, run_random_events

IDLE_AFTER_R0 = Scan(run_id="r0", run_state="complete", run_full=True, generation="g0")


def observe(job, scan, at=10, accepted="g0", rejected=frozenset()):
    return reduce(job, Observed(at=at, scan=scan, accepted=accepted, rejected=rejected))


def running_manual(run_id="r1", run_state="running"):
    return Scan(invocation="inv-1", full=True, run_id=run_id, run_state=run_state, run_full=True)


def dispatched():
    job, effects = observe(admit("q" * 32, "operator", 0), IDLE_AFTER_R0)
    assert (job.state, job.dispatches, effects) == ("dispatching", 1, (Dispatch(),))
    return job


def test_rescan_succeeds_only_after_its_publication_is_accepted():
    job, _ = observe(dispatched(), running_manual())
    assert (job.state, job.invocation, job.agent_run) == ("running", "inv-1", "r1")
    job, _ = reduce(job, DispatchReturned(at=20, ok=True))
    assert job.state == "running", "the SSH exit status decided the job"
    job, _ = observe(job, Scan(run_id="r1", run_state="complete", run_full=True, generation="g1"), at=30)
    assert (job.state, job.generation) == ("collecting", "g1")
    job, _ = observe(job, Scan(run_id="r1", run_state="complete", generation="g1"), at=40, accepted="g1")
    assert (job.state, job.result_code, job.finished_unix) == ("succeeded", "OK", 40)


def test_exit_zero_without_receipt_is_unconfirmed():
    job, _ = reduce(dispatched(), DispatchReturned(at=20, ok=True))
    assert job.state == "reconciling"
    job, _ = observe(job, IDLE_AFTER_R0, at=30)
    assert job.state == "reconciling"
    job, _ = reduce(job, Tick(at=20 + jobs.RECONCILE_DEADLINE_SECONDS))
    assert (job.state, job.result_code) == ("unconfirmed", "NO_TRACE")


def test_c4_scheduled_scan_during_request_stays_pending():
    # Preflight saw an idle host. A partial scheduled scan then took the lock, so the manual start lost it.
    job, _ = reduce(dispatched(), DispatchReturned(at=20, ok=False, code="RESCAN_FAILED"))
    scheduled = Scan(invocation="inv-s", full=False, run_id="r1", run_state="running")
    job, effects = observe(job, scheduled, at=30)
    assert job.state == "pending", f"C4 scheduled scan left accepted rescan {job.state}"
    assert effects == ()
    job, effects = observe(job, Scan(run_id="r1", run_state="complete", generation="g1"), at=40)
    assert (job.state, job.dispatches, effects) == ("dispatching", 2, (Dispatch(),))


def test_running_full_scan_satisfies_the_request_without_a_second_scan():
    scan = Scan(invocation="inv-s", full=True, run_id="r1", run_state="running", run_full=True)
    job = admit("q" * 32, "operator", 0)
    job, effects = observe(job, scan)
    assert (job.state, job.invocation, job.agent_run, effects) == ("running", "inv-s", "r1", ())


def test_tied_scan_failure_fails_the_job_with_the_agent_code():
    job, _ = observe(dispatched(), running_manual())
    job, _ = observe(job, Scan(run_id="r1", run_state="failed", error_code="SCANNER_FAILED"), at=50)
    assert (job.state, job.result_code) == ("failed", "SCANNER_FAILED")


def test_run_replaced_before_we_saw_its_end_is_unconfirmed():
    job, _ = observe(dispatched(), running_manual())
    job, _ = observe(job, Scan(invocation="inv-2", full=False, run_id="r2", run_state="running"), at=50)
    assert (job.state, job.result_code) == ("unconfirmed", "RUN_UNCORRELATED")


def test_rejected_publication_fails_the_job():
    job, _ = observe(dispatched(), running_manual())
    job, _ = observe(job, Scan(run_id="r1", run_state="partial", generation="g1"), rejected=frozenset({"g1"}))
    assert (job.state, job.result_code) == ("failed", "CAPTURE_REJECTED")


def test_deadlines_end_waiting_states():
    pending = Job("q" * 32, "op", 0, 0, state="pending")
    assert reduce(pending, Tick(at=7199))[0].state == "pending"
    assert reduce(pending, Tick(at=7200))[0].result_code == "RESCAN_NOT_STARTED"
    running = Job("q" * 32, "op", 0, 0, state="running", invocation="i")
    assert reduce(running, Tick(at=3 * 3600 - 1, run_deadline_seconds=jobs.run_deadline_seconds(3600)))[0].state == "running"
    assert reduce(running, Tick(at=9 * 3600, run_deadline_seconds=jobs.run_deadline_seconds(3 * 3600)))[0].result_code == "RESCAN_TIMEOUT"
    collecting = Job("q" * 32, "op", 0, 0, state="collecting", generation="g1")
    assert reduce(collecting, Tick(at=jobs.COLLECT_DEADLINE_SECONDS))[0].result_code == "PULL_FAILED"


def test_restart_makes_an_in_flight_dispatch_ambiguous():
    assert reduce(dispatched(), Restarted(at=5))[0].state == "reconciling"
    fresh = admit("q" * 32, "operator", 0)
    assert reduce(fresh, Restarted(at=5))[0] == fresh


# ------------------------------------------------------------------ receipt jobs (v2 hosts)


def requested():
    """A v2 job whose rescan <request_id> the host answered ``received``."""
    job, effects = reduce(admit("q" * 32, "operator", 0, "receipt"), Traced(at=5))
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
        job = Job("q" * 32, "op", 0, 0, state=state, dispatches=1, protocol="receipt")
        for trace in ({}, {"pending": True}, {"running": True}):
            unknown = reduce(job, Traced(at=10, receipt="vanished", **trace))
            assert unknown == reduce(job, Traced(at=10, **trace)), f"an unknown receipt changed a {state} job"
            assert unknown[0].state not in jobs.TERMINAL


def test_ambiguous_reply_reconciles_by_observation_and_resends_the_same_id():
    job, _ = reduce(admit("q" * 32, "operator", 0, "receipt"), Traced(at=5))
    job, _ = reduce(job, RescanReplied(at=6, outcome="ambiguous", code="TIMEOUT"))
    assert job.state == "reconciling"
    job, effects = reduce(job, Traced(at=10))
    assert (job.state, effects, job.dispatches) == ("reconciling", (), 1), "resent before the idle interval"
    job, effects = reduce(job, Traced(at=5 + jobs.RESEND_INTERVAL_SECONDS))
    assert (job.state, effects, job.dispatches) == ("reconciling", (Dispatch(),), 2)
    job, _ = reduce(job, Traced(at=200, receipt="partial"))
    assert (job.state, job.result_code) == ("succeeded", "OK")


def test_rejected_request_fails_and_reconcile_deadline_is_unconfirmed():
    job, _ = reduce(admit("q" * 32, "operator", 0, "receipt"), Traced(at=5))
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
    job, _ = reduce(admit("q" * 32, "operator", 0, "receipt"), Traced(at=5))
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


def test_each_protocol_ignores_the_other_protocols_events():
    receipt = requested()
    assert reduce(receipt, Observed(10, Scan(invocation="i", full=True, run_id="r1", run_state="running")))[0] == receipt
    assert reduce(receipt, DispatchReturned(10, True))[0] == receipt
    invocation = dispatched()
    assert reduce(invocation, Traced(10, receipt="complete"))[0] == invocation
    assert reduce(invocation, RescanReplied(10, "received"))[0] == invocation


def test_job_table_total():
    assert_table_total(jobs.TRANSITIONS, jobs.STATES, jobs.EVENT_KINDS)


def test_job_random_events():
    runs = ("r0", "r1", "r2", None)

    def scan(rng):
        active = rng.random() < 0.5
        return Scan(
            invocation=rng.choice(("inv-1", "inv-2")) if active else None,
            full=rng.random() < 0.5,
            run_id=rng.choice(runs),
            run_state=rng.choice(("running", "complete", "partial", "failed", None)),
            run_full=rng.random() < 0.5,
            generation=rng.choice(("g0", "g1", "g2")),
        )

    def event(rng, job, index):
        at = index * 1800
        kind = rng.randrange(6)
        if kind == 0:
            return Observed(at, scan(rng), rng.choice(("g0", "g1", None)), frozenset(rng.sample(("g1", "g2"), rng.randrange(3))))
        if kind == 1:
            return DispatchReturned(at, rng.random() < 0.5)
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
        assert after.protocol == before.protocol
        if before.state in jobs.TERMINAL:
            assert after == before, "a terminal job changed"
        if after.state == "succeeded" and before.state != "succeeded" and after.protocol == "invocation":
            assert after.generation is not None and before.state in ("running", "reconciling", "collecting")
            assert isinstance(event, Observed) and event.accepted == after.generation, "succeeded without its publication"
        if after.state == "succeeded" and before.state != "succeeded" and after.protocol == "receipt":
            assert isinstance(event, Traced) and event.receipt in ("complete", "partial"), "I24 succeeded without a receipt"
            assert event.captures == "current", "succeeded before its captures were held"
        if isinstance(event, (Traced, RescanReplied)) and after.protocol == "invocation":
            assert after == before, "a receipt event moved an invocation job"
        if isinstance(event, (Observed, DispatchReturned)) and after.protocol == "receipt":
            assert after == before, "an invocation event moved a receipt job"
        if isinstance(event, Traced) and event.receipt not in jobs.RECEIPT_SUCCEEDS | set(jobs.RECEIPT_FAILS):
            assert reduce(before, replace(event, receipt=None)) == (after, effects), "an unknown receipt changed the outcome"
        if effects and after.protocol == "receipt":
            assert after.last_dispatch_unix == event.at
            assert before.last_dispatch_unix is None or event.at - before.last_dispatch_unix >= jobs.RESEND_INTERVAL_SECONDS, \
                "a receipt job resent within the idle interval"
            assert not (isinstance(event, Traced) and event.pending and before.dispatches), "a delivered request was sent again"
        if effects:
            assert effects == (Dispatch(),)
            assert after.state == "dispatching" or (after.protocol == "receipt" and after.state in ("pending", "reconciling"))
            assert after.dispatches == before.dispatches + 1, "dispatched twice for one attempt"
        assert after.request_id == before.request_id, "a second request id for one admitted job"
        assert after.dispatches <= jobs.MAX_DISPATCHES, "the scan was started without limit"
        if isinstance(event, Tick) and event.at - after.requested_unix >= jobs.job_deadline_seconds(event.run_deadline_seconds):
            assert after.state in jobs.TERMINAL, "a job outlived its deadline from admission"

    run_random_events(lambda rng: admit("q" * 32, "op", 0, rng.choice(jobs.PROTOCOLS)), event, reduce, check)


def test_a_job_restarts_its_scan_a_bounded_number_of_times():
    # M2b review: every lost-lock round started the scan again, without limit.
    job = dispatched()
    starts = 1
    for index in range(1, 10):
        job, _ = reduce(job, DispatchReturned(at=index * 100, ok=False, code="RESCAN_FAILED"))
        scheduled = Scan(run_id=f"s{index}", run_state="complete", run_full=False, generation=f"g{index}")
        job, effects = observe(job, scheduled, at=index * 100 + 50)
        starts += len(effects)
        if job.state in jobs.TERMINAL:
            break
    assert starts == jobs.MAX_DISPATCHES
    assert (job.state, job.result_code) == ("failed", "RESCAN_NOT_STARTED")


def test_state_changes_never_postpone_the_deadline_from_admission():
    # M2b review: each transition reset since_unix, so a job that kept changing state never timed out.
    job = dispatched()
    limit = jobs.job_deadline_seconds(jobs.MIN_RUN_DEADLINE_SECONDS)
    job, _ = observe(job, Scan(invocation="inv-s", full=False, run_id="s1", run_state="running"), at=limit - 60)
    assert job.state == "pending"
    assert reduce(job, Tick(at=limit - 1))[0].state == "pending"
    assert reduce(job, Tick(at=limit))[0].result_code == "RESCAN_NOT_STARTED"


def test_a_finished_run_seen_while_the_unit_starts_is_not_the_jobs_run():
    # M2b review: a job admitted without an observation had no baseline, so any finished run was "new".
    job = admit("q" * 32, "operator", 0)
    starting = Scan(invocation="inv-1", full=True, run_id="r0", run_state="complete", run_full=True, generation="g0")
    job, _ = observe(job, starting)
    assert job.state == "running"
    job, _ = observe(job, IDLE_AFTER_R0, at=20)
    assert (job.state, job.result_code) == ("unconfirmed", "RUN_UNCORRELATED")
