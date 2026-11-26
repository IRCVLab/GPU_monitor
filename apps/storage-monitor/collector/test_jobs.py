from collector import jobs
from collector.jobs import Dispatch, DispatchReturned, Job, Observed, Restarted, Scan, Tick, admit, reduce
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
        kind = rng.randrange(4)
        if kind == 0:
            return Observed(at, scan(rng), rng.choice(("g0", "g1", None)), frozenset(rng.sample(("g1", "g2"), rng.randrange(3))))
        if kind == 1:
            return DispatchReturned(at, rng.random() < 0.5)
        if kind == 2:
            return Tick(at, jobs.run_deadline_seconds(rng.choice((None, 600, 7200))))
        return Restarted(at)

    def check(before, event, after, effects):
        assert after.state in jobs.STATES
        if before.state in jobs.TERMINAL:
            assert after == before, "a terminal job changed"
        if after.state == "succeeded" and before.state != "succeeded":
            assert after.generation is not None and before.state in ("running", "reconciling", "collecting")
            assert isinstance(event, Observed) and event.accepted == after.generation, "succeeded without its publication"
        if effects:
            assert effects == (Dispatch(),) and after.state == "dispatching"
            assert after.dispatches == before.dispatches + 1, "dispatched twice for one attempt"
        assert after.request_id == before.request_id, "a second request id for one admitted job"
        assert after.dispatches <= jobs.MAX_DISPATCHES, "the scan was started without limit"
        if isinstance(event, Tick) and event.at - after.requested_unix >= jobs.job_deadline_seconds(event.run_deadline_seconds):
            assert after.state in jobs.TERMINAL, "a job outlived its deadline from admission"

    run_random_events(lambda rng: admit("q" * 32, "op", 0), event, reduce, check)


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
