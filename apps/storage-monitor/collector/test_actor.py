import json
import threading

import pytest

from collector import judge
from collector.actor import Admit, Ask, last_run_duration
from collector.contract import LastRun
from collector.test_contract import VID
from collector.store import ServerStore
from collector.test_bug_harness import Collector, FakeHost, ManualSpawn, make_server, transport_error

INITIAL_START = 1719200000


def capture_start(host, server_id="alpha-1"):
    return host.actor(server_id)._captures[VID][1].scan_started_unix


def admit(actor, at, cooldown=900):
    ask = Ask()
    actor.post(Admit("q" * 32, "operator", at, cooldown, ask))
    actor.step()
    return ask.value


# ------------------------------------------------------------------ bugs


def test_c1_observe_success_clears_unreachable(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.host.observe_errors["alpha-1"] = transport_error("TIMEOUT")
    host.tick()
    assert host.summary()["link"]["state"] == "unreachable"
    host.host.observe_errors.clear()
    host.tick()
    summary = host.summary()
    assert summary["activity"]["observed_unix"] == host.clock.now
    assert summary["link"]["state"] == "ok", "C1 successful observation left the host unreachable"


def test_c1_late_transfer_failure_does_not_mark_reachable_host_unreachable(tmp_path):
    spawn = ManualSpawn()
    host = Collector(tmp_path, "alpha-1", spawn=spawn)
    spawn.run()
    host.settle()
    host.host.publish("alpha-1", started=1719203000)
    host.tick()
    assert spawn.pending, "the new capture should be in flight"
    host.host.fetch_errors["alpha-1"] = transport_error("TIMEOUT")
    host.tick()
    spawn.run()
    host.settle()
    assert host.summary()["link"]["state"] == "ok", "C1 a late transfer timeout undid a newer observe"


def test_c8_restart_downloads_nothing(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    before = len(host.host.capture_calls)
    host.restart()
    host.tick()
    assert host.summary()["link"]["state"] == "ok"
    downloads = len(host.host.capture_calls) - before
    assert downloads == 0, f"C8 restarted collector downloaded {downloads} unchanged snapshots"


# ------------------------------------------------------------------ data path (ported from test_service.py)


def test_unchanged_generation_skips_transfer_and_stays_fresh(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    assert len(host.host.capture_calls) == 1
    for _ in range(5):
        host.tick()
    assert len(host.host.capture_calls) == 1
    summary = host.summary()
    assert (summary["link"]["state"], bool(summary["volumes"]), summary["freshness"]) == ("ok", True, "fresh")


def test_unreachable_and_race_keep_last_good_and_race_is_retried(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.host.observe_errors["alpha-1"] = transport_error("UNREACHABLE")
    host.tick()
    assert (capture_start(host), host.summary()["link"]["state"]) == (INITIAL_START, "unreachable")
    host.host.observe_errors.clear()
    status, _ = host.host.publish("alpha-1", started=1719203000)
    host.host.fetch_errors["alpha-1"] = transport_error("RACE")
    host.tick()
    assert capture_start(host) == INITIAL_START
    host.host.fetch_errors.clear()
    host.tick()
    assert capture_start(host) == 1719203000
    assert host.host.captures_of(status["sha256"]) == 2


def test_size_mismatch_gets_one_clean_retry_then_is_rejected(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    status, _ = host.host.publish("alpha-1", started=1719203000)
    host.host.fetch_errors["alpha-1"] = transport_error("SIZE_MISMATCH")
    for _ in range(10):
        host.tick()
    assert host.host.captures_of(status["sha256"]) == 2
    assert host.summary()["volumes"][0]["data_state"] == "stale_rejected"


def test_oversized_publication_is_rejected_without_retries(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    status, _ = host.host.publish("alpha-1", started=1719203000)
    host.host.fetch_errors["alpha-1"] = transport_error("CAPTURE_TOO_LARGE")
    for _ in range(10):
        host.tick()
    assert host.host.captures_of(status["sha256"]) == 1
    assert capture_start(host) == INITIAL_START


@pytest.mark.parametrize("failure", [MemoryError("transient"), OSError(5, "EIO")])
def test_an_environment_failure_while_validating_a_capture_is_retried_not_rejected(tmp_path, monkeypatch, failure):
    # M2b review: any exception from validation became a permanent INVALID_SNAPSHOT rejection.
    from collector import contract

    host = Collector(tmp_path, "alpha-1")
    status, _ = host.host.publish("alpha-1", started=1719203000)
    validate = contract.parse_capture
    failures = [failure]

    def flaky(*args):
        if failures:
            raise failures.pop()
        return validate(*args)

    monkeypatch.setattr(contract, "parse_capture", flaky)
    host.tick()
    host.tick()
    assert capture_start(host) == 1719203000
    assert host.host.captures_of(status["sha256"]) == 2


def test_config_drift_is_reported_without_refetching(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.servers = [make_server(digest="b" * 64)]
    host.restart()
    host.tick()
    summary = host.summary()
    assert (summary["link"]["state"], host.actor()._record.data.manifest.config_digest != host.servers[0].scanner_digest) == ("ok", True)
    assert len(host.host.capture_calls) == 1


def test_disabled_servers_are_skipped_and_inventory_order_kept(tmp_path):
    host = Collector(tmp_path, "alpha-1", "beta-2")
    host.servers = [make_server("beta-2", order=10), make_server("off-3", order=15, enabled=False), make_server("alpha-1", order=20)]
    host.restart()
    host.tick()
    assert [view.server.id for view in host.fleet.views()] == ["beta-2", "alpha-1"]
    assert "off-3" not in host.host.observe_calls


def test_user_totals_in_overview_survive_restart(tmp_path):
    host = Collector(tmp_path, "alpha-1", publish=False)
    host.host.publish("alpha-1")
    host.tick()
    expected = [{"uid": 1001, "name": "kim", "bytes": 0}]
    assert host.summary()["users_top"] == expected
    host.restart()
    assert host.summary()["users_top"] == expected
    assert len(host.host.capture_calls) == 1


def test_missing_snapshot_is_absent(tmp_path):
    host = Collector(tmp_path, "alpha-1", publish=False)
    summary = host.summary()
    assert summary["volumes"] == []


def test_freshness_follows_retained_snapshot_age_through_failures(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    finished = host.actor()._captures[VID][1].scan_finished_unix
    host.host.observe_errors["alpha-1"] = transport_error("UNREACHABLE")
    host.clock.now = finished + 2 * 3600
    assert host.summary()["freshness"] == "fresh"
    host.clock.now = finished + 8 * 3600
    host.tick()
    assert host.summary()["freshness"] == "stale"


# ------------------------------------------------------------------ transfer limits and scheduling


def test_transfers_never_exceed_two_across_the_fleet(tmp_path):
    entered = threading.Semaphore(0)
    release = threading.Event()
    host = Collector(tmp_path, "alpha-1", "beta-2", "gamma-3", publish=False, spawn=lambda work, name: threading.Thread(target=work, daemon=True).start())
    for sid in ("alpha-1", "beta-2", "gamma-3"):
        host.host.publish(sid)

    def gate(server):
        entered.release()
        assert release.wait(5), "transfer barrier was never released"

    host.host.fetch_gate = gate
    host.tick()
    assert entered.acquire(timeout=5) and entered.acquire(timeout=5), "two transfers should run"
    assert host.host.active_fetches == 2, "a third transfer ran past the fleet bound"
    release.set()
    for sid in ("alpha-1", "beta-2", "gamma-3"):
        actor = host.actor(sid)
        while not host.summary(sid)["volumes"]:
            actor._guard(actor._handle, actor.inbox.get(timeout=5))
    assert host.host.max_active_fetches == 2


def test_observe_interval_is_fast_while_scanning_and_slow_when_idle(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    calls = len(host.host.observe_calls)
    host.tick(judge.ACTIVE_TELEMETRY_INTERVAL_SECONDS)
    assert len(host.host.observe_calls) == calls, "an idle host was observed before 60 s"
    host.host.statuses["alpha-1"] = {"state": "running"}
    host.tick(judge.IDLE_TELEMETRY_INTERVAL_SECONDS)
    host.tick(judge.ACTIVE_TELEMETRY_INTERVAL_SECONDS)
    assert len(host.host.observe_calls) == calls + 2, "a scanning host was not observed every 5 s"


def test_slow_host_does_not_delay_another_hosts_contact(tmp_path):
    host = Collector(tmp_path, "alpha-1", "beta-2")
    entered, release = threading.Event(), threading.Event()

    def gate(server):
        if server.id == "alpha-1":
            entered.set()
            assert release.wait(5), "slow host barrier was never released"

    host.host.observe_gate = gate
    host.clock.now += 61
    slow = threading.Thread(target=host.actor("alpha-1").step)
    slow.start()
    try:
        assert entered.wait(5)
        before = host.host.observe_calls.count("beta-2")
        host.actor("beta-2").step()
        assert host.host.observe_calls.count("beta-2") == before + 1
        assert host.summary("beta-2")["activity"]["observed_unix"] == host.clock.now
    finally:
        release.set()
        slow.join(5)


# ------------------------------------------------------------------ isolation


def test_unreadable_store_isolates_one_server(tmp_path):
    host = Collector(tmp_path, "alpha-1", "beta-2")
    healthy = host.summary("alpha-1")
    (host.state_dir / "servers" / "beta-2" / "record.json").write_text("{")
    host.restart()
    host.tick()
    assert host.fleet.view("beta-2").error == "STORE_UNAVAILABLE"
    assert host.summary("beta-2")["status"] == "error"
    assert "beta-2" not in host.host.observe_calls[-1:], "a server without a readable record contacted its host"
    after = host.summary("alpha-1")
    volatile = {"activity", "link"}  # the tick is a newer contact: last_ok_unix moves on
    assert {key: after[key] for key in healthy if key not in volatile} == {key: healthy[key] for key in healthy if key not in volatile}
    assert after["link"]["state"] == "ok"


def test_a_transient_capture_read_error_at_startup_keeps_the_record_and_the_capture(tmp_path, monkeypatch):
    # M2b review: a read error was treated like corrupt data, so startup dropped and deleted the last good capture.
    from collector.errors import StoreUnavailable

    host = Collector(tmp_path, "alpha-1")
    directory = host.state_dir / "servers" / "alpha-1"
    files = {path.name: path.read_bytes() for path in (directory / "captures").iterdir()}
    record = (directory / "record.json").read_bytes()
    read = ServerStore.read_capture
    failures = [StoreUnavailable("capture unreadable")]

    def flaky(store, volume_id, sha):
        if failures:
            raise failures.pop()
        return read(store, volume_id, sha)

    monkeypatch.setattr(ServerStore, "read_capture", flaky)
    host.host.observe_errors["alpha-1"] = transport_error("UNREACHABLE")
    host.restart()
    assert host.fleet.view("alpha-1").error == "STORE_UNAVAILABLE"
    assert {path.name: path.read_bytes() for path in (directory / "captures").iterdir()} == files
    assert (directory / "record.json").read_bytes() == record
    host.host.observe_errors.clear()
    host.tick()
    assert host.summary()["volumes"]
    assert host.fleet.view("alpha-1").error is None
    assert host.host.capture_calls == [("alpha-1", VID, host.actor()._captures[VID][0])], "the stored capture was downloaded again"


def test_a_corrupt_stored_capture_is_fetched_again_and_not_deleted_on_discovery(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    sha = host.actor()._captures[VID][0]
    capture = host.state_dir / "servers" / "alpha-1" / "captures" / f"{sha}.json.gz"
    capture.write_bytes(b"{not json")
    host.host.observe_errors["alpha-1"] = transport_error("UNREACHABLE")
    host.restart()
    assert capture.exists(), "the capture was deleted in the step that found it corrupt"
    assert host.summary()["volumes"][0]["capture"] is None
    host.host.observe_errors.clear()
    host.tick()
    assert host.summary()["volumes"]
    assert host.host.captures_of(sha) == 2


def test_reducer_failure_keeps_last_good_view_and_other_servers_run(tmp_path, monkeypatch):
    host = Collector(tmp_path, "alpha-1", "beta-2")
    before = host.fleet.view("alpha-1").publication

    def broken(server):
        if server.id == "alpha-1":
            raise RuntimeError("bug")
        return FakeHost.observe(host.host, server)

    monkeypatch.setattr(host.host, "observe", broken)
    host.tick()
    assert host.fleet.view("alpha-1").error == "TICK_FAILED"
    assert host.fleet.view("alpha-1").publication is before
    assert host.fleet.view("beta-2").error is None


def test_a_failed_tick_stops_showing_the_error_after_the_next_good_tick(tmp_path):
    # M2b review: TICK_FAILED stayed on the server until restart.
    host = Collector(tmp_path, "alpha-1")
    host.host.observe_errors["alpha-1"] = RuntimeError("one bad reply")
    host.tick()
    assert host.summary()["status"] == "error"
    host.host.observe_errors.clear()
    host.tick()
    assert host.summary()["status"] == "ok"
    assert host.fleet.view("alpha-1").error is None


def test_a_failed_tick_backs_off_instead_of_contacting_the_host_again_at_once(tmp_path):
    # M2b review: a failure before the next contact was scheduled left it in the past, so the actor spun.
    host = Collector(tmp_path, "alpha-1")
    host.host.observe_errors["alpha-1"] = RuntimeError("bug")
    host.clock.now += judge.IDLE_TELEMETRY_INTERVAL_SECONDS + 1
    before = len(host.host.observe_calls)
    for _ in range(20):
        host.actor().step()
    assert len(host.host.observe_calls) - before == 1
    gaps = []
    for _ in range(8):
        calls = len(host.host.observe_calls)
        waited = 0
        while len(host.host.observe_calls) == calls:
            host.tick(1)
            waited += 1
        gaps.append(waited)
    assert gaps == sorted(gaps) and gaps[0] >= judge.IDLE_TELEMETRY_INTERVAL_SECONDS, f"retries came after {gaps} s"
    assert gaps[-1] == gaps[-2], "the back-off is not capped"
    host.host.observe_errors.clear()
    host.tick(gaps[-1])
    host.tick(judge.IDLE_TELEMETRY_INTERVAL_SECONDS)
    calls = len(host.host.observe_calls)
    host.tick(judge.IDLE_TELEMETRY_INTERVAL_SECONDS)
    assert len(host.host.observe_calls) == calls + 1, "a good tick did not restore the normal interval"


def test_a_failure_while_applying_a_capture_does_not_block_later_fetches(tmp_path, monkeypatch):
    # M2b review: an exception before commit left data.fetching set, so no capture was fetched again.
    import collector.actor as actor_module

    host = Collector(tmp_path, "alpha-1")
    real = actor_module.publication
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise MemoryError("transient")
        return real(*args, **kwargs)

    monkeypatch.setattr(actor_module, "publication", flaky)
    host.host.publish("alpha-1", started=1719203000)
    host.tick()
    assert capture_start(host) == INITIAL_START
    host.tick()
    assert capture_start(host) == 1719203000


def test_commit_failure_keeps_memory_at_the_last_durable_record(tmp_path, monkeypatch):
    host = Collector(tmp_path, "alpha-1")
    host.host.publish("alpha-1", started=1719203000)

    def full(*args, **kwargs):
        from collector.errors import StoreUnavailable
        raise StoreUnavailable("disk full")

    monkeypatch.setattr(host.actor().store, "commit", full)
    host.tick()
    assert host.fleet.view("alpha-1").error == "STORE_UNAVAILABLE"
    assert capture_start(host) == INITIAL_START
    monkeypatch.undo()
    host.tick()
    assert capture_start(host) == 1719203000
    assert host.fleet.view("alpha-1").error is None


# ------------------------------------------------------------------ old store import (owner decision, R1, S6)


# ------------------------------------------------------------------ ETA history


# ------------------------------------------------------------------ rescan job through the actor


def test_rescan_admission_refuses_active_job_cooldown_and_unloaded_store(tmp_path):
    host = Collector(tmp_path, "alpha-1", spawn=ManualSpawn())
    actor = host.actor()
    now = int(host.clock.now)
    assert admit(actor, now).error is None
    assert admit(actor, now).error == "ACTIVE_JOB"
    record = actor._record
    actor._record = type(record)(link=record.link, data=record.data, last_job=record.job, durations=record.durations)
    cooled = admit(actor, now + 899)
    assert (cooled.error, cooled.retry_after) == ("COOLDOWN", 1)
    (host.state_dir / "servers" / "alpha-1" / "record.json").write_text("{")
    host.restart()
    assert admit(host.actor(), now).error == "STORE_UNAVAILABLE"


def test_admission_after_the_asker_gave_up_creates_no_job(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    actor = host.actor()
    ask = Ask()
    actor.post(Admit("q" * 32, "operator", int(host.clock.now), 900, ask))
    assert ask.wait(0) is None
    actor.step()
    assert host.latest_job() is None


def test_job_and_cooldown_survive_restart(tmp_path):
    host = Collector(tmp_path, "alpha-1", spawn=ManualSpawn())
    now = int(host.clock.now)
    assert admit(host.actor(), now).error is None
    host.restart()
    job = host.latest_job()
    assert (job["state"], job["state"]) == ("reconciling", "reconciling"), "an in-flight start must become ambiguous after restart"
    host.clock.now += 20 * 60
    host.settle()
    assert host.latest_job()["state"] == "unconfirmed"
    host.restart()
    cooled = admit(host.actor(), now + 899)
    assert (cooled.error, cooled.retry_after) == ("COOLDOWN", 1)


def test_fleet_start_serves_stored_data_from_the_first_read(tmp_path):
    from collector.fleet import Fleet
    from collector.test_bug_harness import inline

    host = Collector(tmp_path, "alpha-1")
    fleet = Fleet(host.servers, host.state_dir, host.host, clock=host.clock, spawn=inline)
    fleet.start()
    try:
        assert fleet.view("alpha-1").publication.captures
        with pytest.raises(SystemExit):
            Fleet(host.servers, host.state_dir, host.host, clock=host.clock).start()
    finally:
        fleet.stop()


def test_rescan_request_answers_within_the_reply_bound_while_the_admission_commit_stalls(tmp_path):
    # M2b review: once the actor claimed the request, the HTTP thread waited for it without a bound (C5).
    import time

    from collector.errors import ActiveJob, GlobalConcurrency, ServerBusy

    host = Collector(tmp_path, "alpha-1", "beta-2", spawn=ManualSpawn())
    host.restart(max_active_jobs=1)
    host.fleet.reply_timeout = 0.5
    actor = host.actor()
    stalled, release = threading.Event(), threading.Event()
    commit = actor.store.commit

    def slow_commit(record, capture=None):
        if record.job is not None:
            stalled.set()
            release.wait(20)
        return commit(record, capture)

    actor.store.commit = slow_commit
    post = actor.post

    def post_and_handle(message):
        post(message)
        threading.Thread(target=actor.step, daemon=True).start()

    actor.post = post_and_handle
    started = time.monotonic()
    with pytest.raises(ServerBusy):
        host.fleet.request_rescan("alpha-1", "operator")
    assert time.monotonic() - started < 1.5, "the request waited past the reply bound"
    assert stalled.is_set(), "the actor never took the request"
    with pytest.raises(GlobalConcurrency):
        host.fleet.request_rescan("beta-2", "operator")
    release.set()
    deadline = time.monotonic() + 5
    while host.latest_job() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert host.latest_job()["state"] == "dispatching", "the admission that finished late was lost"
    with pytest.raises(ActiveJob):
        host.fleet.request_rescan("alpha-1", "operator")


def test_idle_observes_do_not_rewrite_the_record(tmp_path, monkeypatch):
    # M2b review: record.json was written and fsynced on every observe only because link.seq changed.
    host = Collector(tmp_path, "alpha-1")
    host.tick()
    writes = []
    commit = ServerStore.commit
    monkeypatch.setattr(ServerStore, "commit", lambda store, *args, **kwargs: (writes.append(args), commit(store, *args, **kwargs)))
    observes = len(host.host.observe_calls)
    for _ in range(20):
        host.tick()
    assert len(host.host.observe_calls) - observes == 20
    assert writes == [], f"{len(writes)} observes that changed nothing durable rewrote the record"
    host.host.publish("alpha-1", started=1719203000)
    host.tick()
    assert writes and ServerStore(host.state_dir, "alpha-1").load().data.accepted == {VID: host.actor()._captures[VID][0]}


def test_contact_sequence_numbers_keep_increasing_across_a_restart(tmp_path, monkeypatch):
    import collector.actor as actor_module
    from collector import health

    monkeypatch.setattr(actor_module, "SEQ_BLOCK", 3)
    applied = []
    reduce_link = health.reduce_link

    def spy(link, event):
        applied.append(event.seq)
        return reduce_link(link, event)

    monkeypatch.setattr(health, "reduce_link", spy)
    host = Collector(tmp_path, "alpha-1")
    for started in range(1719203000, 1719203010):
        host.host.publish("alpha-1", started=started)
        host.tick()
    for _ in range(4):
        host.tick()  # seqs issued after the last durable change
    before = list(applied)
    host.restart()
    for _ in range(10):
        host.tick()
    after = applied[len(before):]
    assert before and after
    assert after[0] > max(before), f"seq {after[0]} after restart is not above {max(before)}"
    assert applied == sorted(set(applied)), "contact sequence numbers repeated or went back"


def test_duration_history_retains_five_distinct_completions_per_plan():
    history = {}
    for i in range(1, 8):
        run = LastRun("1" * 32, "timer", "full", i * 100, i * 110, "complete")
        history = last_run_duration(history, run)
    assert history == {"full": [[i * 110, i * 10] for i in range(3, 8)]}
    assert last_run_duration(history, run) is history
