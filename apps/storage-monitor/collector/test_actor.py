import json
import threading

import pytest

from collector import judge
from collector.actor import Admit, Ask, record_duration
from collector.jobs import Scan
from collector.store import ServerStore
from collector.test_bug_harness import Collector, FakeHost, ManualSpawn, make_server, payload_for, status_data, transport_error

GENERATION = "alpha-1-1719200000-v1"


def snapshot_generation(host, server_id="alpha-1"):
    return host.summary(server_id)["overview_snapshot"]["scan_generation"]


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
    assert host.summary()["latest_pull_status"] == "unreachable"
    host.host.observe_errors.clear()
    host.tick()
    summary = host.summary()
    assert summary["scan_status"]["observed_unix"] == host.clock.now
    assert summary["latest_pull_status"] == "succeeded", "C1 successful observation left the host unreachable"


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
    assert host.summary()["latest_pull_status"] == "succeeded", "C1 a late transfer timeout undid a newer observe"


def test_c2_rejected_capture_fetched_once(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    payload = payload_for(started=1719203000)
    payload["run_as_root"] = "yes"
    status, data = status_data(payload)
    host.host.publications["alpha-1"], host.host.captures[status["sha256"]] = status, data
    for _ in range(100):
        host.tick()
    assert host.summary()["latest_pull_status"] == "invalid_snapshot"
    assert snapshot_generation(host) == GENERATION, "the last good snapshot was replaced"
    host.restart()
    host.tick()
    attempts = host.host.fetches_of(status["sha256"])
    assert attempts == 1, f"C2 rejected capture fetched {attempts} times across 100 ticks and restart"


def test_c8_restart_downloads_nothing(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    before = len(host.host.fetch_calls)
    host.restart()
    host.tick()
    assert host.summary()["latest_pull_status"] == "succeeded"
    downloads = len(host.host.fetch_calls) - before
    assert downloads == 0, f"C8 restarted collector downloaded {downloads} unchanged snapshots"


# ------------------------------------------------------------------ data path (ported from test_service.py)


def test_unchanged_generation_skips_transfer_and_stays_fresh(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    assert len(host.host.fetch_calls) == 1
    for _ in range(5):
        host.tick()
    assert len(host.host.fetch_calls) == 1
    summary = host.summary()
    assert (summary["latest_pull_status"], summary["snapshot_availability"], summary["freshness"]) == ("succeeded", "available", "fresh")


def test_new_partial_snapshot_applies_and_failed_root_stays_visible(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.host.publish("alpha-1", started=1719201000, partial=True)
    host.tick()
    summary = host.summary()
    assert snapshot_generation(host) == "alpha-1-1719201000-v1"
    assert summary["latest_scan_result"] == "partial"


def test_remote_failed_status_keeps_last_good_with_failed_scan_result(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    failed, _ = status_data(payload_for(started=1719201000), "complete")
    host.host.publications["alpha-1"] = dict(failed, status="failed")
    host.tick()
    summary = host.summary()
    assert snapshot_generation(host) == GENERATION
    assert (summary["latest_scan_result"], summary["latest_pull_status"]) == ("failed", "succeeded")


def test_total_failure_payload_is_rejected_and_keeps_last_good(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    status, data = status_data(payload_for(started=1719202000, total_failed=True), "failed")
    host.host.publications["alpha-1"] = dict(status, status="partial")
    host.host.captures[status["sha256"]] = data
    host.tick()
    assert snapshot_generation(host) == GENERATION
    assert host.summary()["latest_pull_status"] == "invalid_snapshot"


def test_unreachable_and_race_keep_last_good_and_race_is_retried(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.host.observe_errors["alpha-1"] = transport_error("UNREACHABLE")
    host.tick()
    assert (snapshot_generation(host), host.summary()["latest_pull_status"]) == (GENERATION, "unreachable")
    host.host.observe_errors.clear()
    status, _ = host.host.publish("alpha-1", started=1719203000)
    host.host.fetch_errors["alpha-1"] = transport_error("RACE")
    host.tick()
    assert snapshot_generation(host) == GENERATION
    host.host.fetch_errors.clear()
    host.tick()
    assert snapshot_generation(host) == "alpha-1-1719203000-v1"
    assert host.host.fetches_of(status["sha256"]) == 2


def test_size_mismatch_gets_one_clean_retry_then_is_rejected(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    status, _ = host.host.publish("alpha-1", started=1719203000)
    host.host.fetch_errors["alpha-1"] = transport_error("SIZE_MISMATCH")
    for _ in range(10):
        host.tick()
    assert host.host.fetches_of(status["sha256"]) == 2
    assert host.summary()["latest_pull_status"] == "invalid_snapshot"


def test_oversized_publication_is_rejected_without_retries(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    status, _ = host.host.publish("alpha-1", started=1719203000)
    host.host.fetch_errors["alpha-1"] = transport_error("SNAPSHOT_TOO_LARGE")
    for _ in range(10):
        host.tick()
    assert host.host.fetches_of(status["sha256"]) == 1
    assert snapshot_generation(host) == GENERATION


@pytest.mark.parametrize("failure", [MemoryError("transient"), OSError(5, "EIO")])
def test_an_environment_failure_while_validating_a_capture_is_retried_not_rejected(tmp_path, monkeypatch, failure):
    # M2b review: any exception from validation became a permanent INVALID_SNAPSHOT rejection.
    from collector import snapshot

    host = Collector(tmp_path, "alpha-1")
    status, _ = host.host.publish("alpha-1", started=1719203000)
    validate = snapshot.validate_download
    failures = [failure]

    def flaky(*args):
        if failures:
            raise failures.pop()
        return validate(*args)

    monkeypatch.setattr(snapshot, "validate_download", flaky)
    host.tick()
    host.tick()
    assert snapshot_generation(host) == "alpha-1-1719203000-v1"
    assert host.host.fetches_of(status["sha256"]) == 2


def test_config_drift_is_reported_without_refetching(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.servers = [make_server(digest="b" * 64)]
    host.restart()
    host.tick()
    summary = host.summary()
    assert (summary["latest_pull_status"], summary["configuration_sync"]) == ("succeeded", "drifted")
    assert len(host.host.fetch_calls) == 1


def test_disabled_servers_are_skipped_and_inventory_order_kept(tmp_path):
    host = Collector(tmp_path, "alpha-1", "beta-2")
    host.servers = [make_server("beta-2", order=10), make_server("off-3", order=15, enabled=False), make_server("alpha-1", order=20)]
    host.restart()
    host.tick()
    assert [view.server.id for view in host.fleet.views()] == ["beta-2", "alpha-1"]
    assert "off-3" not in host.host.observe_calls


def test_user_totals_in_overview_survive_restart(tmp_path):
    host = Collector(tmp_path, "alpha-1", publish=False)
    payload = payload_for()
    payload["users"] = [
        {"uid": 1002, "name": "beta", "bytes": 20, "files": 1, "by_mount": {"/home": 20}},
        {"uid": 1001, "name": "alpha", "bytes": 10, "files": 1, "by_mount": {"/home": 10}},
    ]
    status, data = status_data(payload)
    host.host.publications["alpha-1"], host.host.captures[status["sha256"]] = status, data
    host.tick()
    expected = [{"uid": 1002, "name": "beta", "bytes": 20}, {"uid": 1001, "name": "alpha", "bytes": 10}]
    assert host.summary()["overview_snapshot"]["users"] == expected
    host.restart()
    assert host.summary()["overview_snapshot"]["users"] == expected
    assert len(host.host.fetch_calls) == 1


def test_missing_snapshot_is_absent(tmp_path):
    host = Collector(tmp_path, "alpha-1", publish=False)
    summary = host.summary()
    assert (summary["snapshot_availability"], summary["overview_snapshot"]) == ("absent", None)


def test_freshness_follows_retained_snapshot_age_through_failures(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    finished = host.host.publications["alpha-1"]["scan_finished_unix"]
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
        while host.summary(sid)["snapshot_availability"] != "available":
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
        assert host.summary("beta-2")["scan_status"]["observed_unix"] == host.clock.now
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
    assert {key: after[key] for key in healthy if key != "scan_status"} == {key: healthy[key] for key in healthy if key != "scan_status"}


def test_a_transient_capture_read_error_at_startup_keeps_the_record_and_the_capture(tmp_path, monkeypatch):
    # M2b review: a read error was treated like corrupt data, so startup dropped and deleted the last good capture.
    from collector.errors import StoreUnavailable

    host = Collector(tmp_path, "alpha-1")
    directory = host.state_dir / "servers" / "alpha-1"
    files = {path.name: path.read_bytes() for path in (directory / "captures").iterdir()}
    record = (directory / "record.json").read_bytes()
    read = ServerStore.read_capture
    failures = [StoreUnavailable("capture unreadable")]

    def flaky(store, sha):
        if failures:
            raise failures.pop()
        return read(store, sha)

    monkeypatch.setattr(ServerStore, "read_capture", flaky)
    host.host.observe_errors["alpha-1"] = transport_error("UNREACHABLE")
    host.restart()
    assert host.fleet.view("alpha-1").error == "STORE_UNAVAILABLE"
    assert {path.name: path.read_bytes() for path in (directory / "captures").iterdir()} == files
    assert (directory / "record.json").read_bytes() == record
    host.host.observe_errors.clear()
    host.tick()
    assert host.summary()["snapshot_availability"] == "available"
    assert host.fleet.view("alpha-1").error is None
    assert host.host.fetch_calls == [("alpha-1", host.host.publications["alpha-1"]["sha256"])], "the stored capture was downloaded again"


def test_a_corrupt_stored_capture_is_fetched_again_and_not_deleted_on_discovery(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    sha = host.host.publications["alpha-1"]["sha256"]
    capture = host.state_dir / "servers" / "alpha-1" / "captures" / f"{sha}.json"
    capture.write_bytes(b"{not json")
    host.host.observe_errors["alpha-1"] = transport_error("UNREACHABLE")
    host.restart()
    assert capture.exists(), "the capture was deleted in the step that found it corrupt"
    assert host.summary()["snapshot_availability"] == "absent"
    host.host.observe_errors.clear()
    host.tick()
    assert host.summary()["snapshot_availability"] == "available"
    assert host.host.fetches_of(sha) == 2


def test_reducer_failure_keeps_last_good_view_and_other_servers_run(tmp_path, monkeypatch):
    host = Collector(tmp_path, "alpha-1", "beta-2")
    before = host.summary("alpha-1")["overview_snapshot"]

    def broken(server):
        if server.id == "alpha-1":
            raise RuntimeError("bug")
        return FakeHost.observe(host.host, server)

    monkeypatch.setattr(host.host, "observe", broken)
    host.tick()
    assert host.fleet.view("alpha-1").error == "TICK_FAILED"
    assert host.summary("alpha-1")["overview_snapshot"] == before
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
    real = actor_module.render_snapshot
    calls = []

    def flaky(payload):
        calls.append(1)
        if len(calls) == 1:
            raise MemoryError("transient")
        return real(payload)

    monkeypatch.setattr(actor_module, "render_snapshot", flaky)
    host.host.publish("alpha-1", started=1719203000)
    host.tick()
    assert snapshot_generation(host) == GENERATION
    host.tick()
    assert snapshot_generation(host) == "alpha-1-1719203000-v1"


def test_commit_failure_keeps_memory_at_the_last_durable_record(tmp_path, monkeypatch):
    host = Collector(tmp_path, "alpha-1")
    host.host.publish("alpha-1", started=1719203000)

    def full(*args, **kwargs):
        from collector.errors import StoreUnavailable
        raise StoreUnavailable("disk full")

    monkeypatch.setattr(host.actor().store, "commit", full)
    host.tick()
    assert host.fleet.view("alpha-1").error == "STORE_UNAVAILABLE"
    assert snapshot_generation(host) == GENERATION
    monkeypatch.undo()
    host.tick()
    assert snapshot_generation(host) == "alpha-1-1719203000-v1"
    assert host.fleet.view("alpha-1").error is None


# ------------------------------------------------------------------ old store import (owner decision, R1, S6)


def write_old_store(state_dir, server_id="alpha-1", job_state="succeeded"):
    status, data = status_data(payload_for(server_id))
    old = state_dir / server_id
    old.mkdir(parents=True)
    snapshot_name = f"snapshot-v2-{status['sha256']}.json"
    state_name = "state-v2-" + "0" * 32 + ".json"
    (old / snapshot_name).write_text(json.dumps(json.loads(data), sort_keys=True))
    job = {"id": "rescan-alpha-1-1-1", "server_id": server_id, "kind": "rescan", "state": job_state, "actor": "op",
           "requested_unix": 1719200000, "started_unix": 1719200000,
           "finished_unix": None if job_state == "running" else 1719200100, "result_code": None if job_state == "running" else "OK"}
    (old / state_name).write_text(json.dumps({"active_job": job, "latest_pull_status": "succeeded"}))
    (old / "current.json").write_text(json.dumps({"snapshot": snapshot_name, "state": state_name}))
    history = state_dir / "scan-duration-history"
    history.mkdir()
    (history / f"{server_id}.json").write_text(json.dumps({"full": [[1719100000, 3000], [1719150000, 3400]]}))
    return status


def test_cutover_imports_the_old_store_without_downloading(tmp_path):
    state_dir = tmp_path / "state"
    status = write_old_store(state_dir)
    before = sorted(path.relative_to(state_dir) for path in state_dir.rglob("*"))
    host = Collector(tmp_path, "alpha-1", publish=False)
    host.host.publications["alpha-1"] = status
    host.tick()
    summary = host.summary()
    assert host.host.fetch_calls == [], "the cutover downloaded a snapshot the old store held"
    assert summary["overview_snapshot"]["scan_generation"] == GENERATION
    assert summary["active_job"]["state"] == "succeeded"
    assert host.fleet.view("alpha-1").durations == {"full": [[1719100000, 3000], [1719150000, 3400]]}
    old_files = sorted(path.relative_to(state_dir) for path in state_dir.rglob("*") if not str(path.relative_to(state_dir)).startswith(("servers", "dashboard")))
    assert old_files == before, "the old store changed"


def test_import_is_idempotent_and_runs_only_without_a_record(tmp_path):
    state_dir = tmp_path / "state"
    write_old_store(state_dir, job_state="running")
    store = ServerStore(state_dir, "alpha-1")
    first = store.import_legacy()
    assert first == store.import_legacy()
    assert first[0].last_job.result_code == "INTERRUPTED"
    host = Collector(tmp_path, "alpha-1", publish=False)
    record = (state_dir / "servers" / "alpha-1" / "record.json").read_bytes()
    host.restart()
    assert (state_dir / "servers" / "alpha-1" / "record.json").read_bytes() == record


# ------------------------------------------------------------------ ETA history


def test_completed_full_and_ssd_durations_feed_the_eta_and_survive_restart(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.host.statuses["alpha-1"] = {"state": "complete", "scan_plan": "full", "last_duration_sec": 900, "finished_unix": 1719300000}
    host.tick()
    host.host.statuses["alpha-1"] = {"state": "running", "scan_plan": "full"}
    host.restart()
    host.tick()
    status = host.summary()["scan_status"]
    assert (status["estimate_duration_sec"], status["estimate_max_duration_sec"]) == (900, 900)


def test_record_duration_keeps_five_successful_completions_once_each():
    durations = {}
    for finished in range(1, 8):
        durations = record_duration(durations, {"state": "complete", "scan_plan": "full", "last_duration_sec": finished * 10, "finished_unix": finished})
    assert durations == {"full": [[3, 30], [4, 40], [5, 50], [6, 60], [7, 70]]}
    assert record_duration(durations, {"state": "complete", "scan_plan": "full", "last_duration_sec": 1, "finished_unix": 7}) is durations
    for status in ({"state": "partial", "scan_plan": "full", "last_duration_sec": 5, "finished_unix": 9},
                   {"state": "complete", "scan_plan": None, "last_duration_sec": 5, "finished_unix": 9}):
        assert record_duration(durations, status) is durations


# ------------------------------------------------------------------ rescan job through the actor


def test_rescan_starts_ties_to_the_unit_and_succeeds_on_the_new_publication(tmp_path):
    spawn = ManualSpawn()
    host = Collector(tmp_path, "alpha-1", spawn=spawn)
    spawn.run()
    host.settle()
    host.host.scans["alpha-1"] = Scan(run_id="r" * 32, run_state="complete", run_full=True, generation=GENERATION)
    host.tick()
    actor = host.actor()
    reply = admit(actor, int(host.clock.now))
    assert reply.error is None and host.summary()["active_job"]["state"] == "running"
    assert spawn.run("storage-viz-rescan") == 1, "the job did not start the scan"
    assert host.summary()["active_job"]["phase"] == "dispatching"
    host.host.scans["alpha-1"] = Scan(invocation="1" * 32, full=True, run_id="s" * 32, run_state="running", run_full=True)
    host.tick(5)
    assert host.summary()["active_job"]["phase"] == "running"
    status, _ = host.host.publish("alpha-1", started=1719300000)
    host.host.scans["alpha-1"] = Scan(run_id="s" * 32, run_state="complete", run_full=True, generation="alpha-1-1719300000-v1")
    host.tick(5)
    assert host.summary()["active_job"]["phase"] == "collecting"
    spawn.run()
    host.settle()
    job = host.summary()["active_job"]
    assert (job["state"], job["phase"], job["result_code"]) == ("succeeded", "succeeded", "OK")


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
    assert host.summary()["active_job"] is None


def test_job_and_cooldown_survive_restart(tmp_path):
    host = Collector(tmp_path, "alpha-1", spawn=ManualSpawn())
    now = int(host.clock.now)
    assert admit(host.actor(), now).error is None
    host.restart()
    job = host.summary()["active_job"]
    assert (job["state"], job["phase"]) == ("running", "reconciling"), "an in-flight start must become ambiguous after restart"
    host.clock.now += 20 * 60
    host.settle()
    assert host.summary()["active_job"]["phase"] == "unconfirmed"
    host.restart()
    cooled = admit(host.actor(), now + 899)
    assert (cooled.error, cooled.retry_after) == ("COOLDOWN", 1)


def test_fleet_start_serves_stored_data_from_the_first_read(tmp_path):
    from collector.fleet import Fleet
    from collector.test_bug_harness import inline
    from collector.view import summary_v1

    host = Collector(tmp_path, "alpha-1")
    fleet = Fleet(host.servers, host.state_dir, host.host, clock=host.clock, spawn=inline)
    fleet.start()
    try:
        assert summary_v1(fleet.view("alpha-1"), host.clock.monotonic())["snapshot_availability"] == "available"
        with pytest.raises(SystemExit):
            Fleet(host.servers, host.state_dir, host.host, clock=host.clock).start()
    finally:
        fleet.stop()


def test_manual_run_that_fails_before_its_plan_fails_the_job_without_redispatch(tmp_path):
    # M2b review: a manual run that failed before writing scan_plan looked like a lost lock (C4),
    # so the job restarted the scan in a tight loop and no deadline ever fired.
    spawn = ManualSpawn()
    host = Collector(tmp_path, "alpha-1", spawn=spawn)
    spawn.run()
    host.settle()
    host.host.scans["alpha-1"] = Scan(run_id="0" * 32, run_state="complete", run_full=True)
    host.tick()
    runs = iter(range(1, 1000))

    def start(server):
        host.host.rescans.append(server.id)
        # The run fails at preflight (NO_SAFE_ROOTS) and is over before the next observe sees the unit.
        host.host.scans["alpha-1"] = Scan(run_id=f"{next(runs):032x}", run_state="failed", error_code="NO_SAFE_ROOTS")

    host.host.start_rescan = start
    assert admit(host.actor(), int(host.clock.now)).error is None
    for _ in range(300):
        host.tick(5)
        spawn.run()
        host.settle()
        if host.summary()["active_job"]["phase"] in ("succeeded", "failed", "unconfirmed"):
            break
    job = host.summary()["active_job"]
    assert (job["phase"], job["result_code"]) == ("failed", "NO_SAFE_ROOTS")
    assert len(host.host.rescans) == 1, f"the scan was started {len(host.host.rescans)} times"


def test_rescan_admitted_before_any_observation_cannot_succeed_from_an_old_run(tmp_path):
    # M2b review: admission with no observed scan left the job without a baseline, so the host's
    # old completed run counted as the job's run and the job succeeded though nothing ran.
    host = Collector(tmp_path, "alpha-1")
    host.host.scans["alpha-1"] = Scan(run_id="0" * 32, run_state="complete", run_full=True, generation=GENERATION)
    host.restart()
    host.host.observe_errors["alpha-1"] = transport_error("TIMEOUT")
    assert admit(host.actor(), int(host.clock.now)).error is None
    host.settle()
    host.host.observe_errors.clear()
    host.host.rescan_errors["alpha-1"] = transport_error("RESCAN_FAILED")
    for _ in range(300):
        host.tick(5)
        if host.summary()["active_job"]["phase"] in ("succeeded", "failed", "unconfirmed"):
            break
    job = host.summary()["active_job"]
    assert (job["phase"], job["result_code"]) == ("unconfirmed", "NO_TRACE"), "a rescan that never ran ended " + job["phase"]


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
    while host.summary()["active_job"] is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert host.summary()["active_job"]["phase"] == "dispatching", "the admission that finished late was lost"
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
    assert writes and ServerStore(host.state_dir, "alpha-1").load().data.accepted == host.host.publications["alpha-1"]["sha256"]


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
