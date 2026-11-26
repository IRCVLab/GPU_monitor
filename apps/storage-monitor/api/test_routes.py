import http.client
import threading
from concurrent.futures import ThreadPoolExecutor


def rescan_headers(request, origin):
    code, session, headers = request("GET", "/api/session")
    assert code == 200 and session["can_rescan"]
    return {"Origin": origin, "Cookie": headers["Set-Cookie"].split(";", 1)[0], "X-CSRF-Token": session["csrf_token"]}


def test_c3_fleet_isolates_unreadable_server(collector_api, monkeypatch):
    from api import server as serve

    host, request, _ = collector_api
    code, fleet, _ = request("GET", "/api/servers")
    assert code == 200
    healthy = next(row for row in fleet["servers"] if row["id"] == "alpha-1")
    (host.state_dir / "servers" / "beta-2" / "record.json").write_text("{")
    host.restart()
    host.use_fleet()
    host.tick()
    try:
        code, fleet, _ = request("GET", "/api/servers")
    except http.client.RemoteDisconnected:
        raise AssertionError("C3 unreadable server disconnected the fleet request") from None
    assert code == 200
    alpha = next(row for row in fleet["servers"] if row["id"] == "alpha-1")
    assert {k: v for k, v in alpha.items() if k != "scan_status"} == {k: v for k, v in healthy.items() if k != "scan_status"}
    broken = next(row for row in fleet["servers"] if row.get("id", row.get("server_id")) == "beta-2")
    assert broken["error"]["code"] == "STORE_UNAVAILABLE", "C3 broken server row is not a typed error"

    render = serve.routes.summary_v1
    monkeypatch.setattr(serve.routes, "summary_v1", lambda view, now: (_ for _ in ()).throw(RuntimeError("bug")) if view.server.id == "beta-2" else render(view, now))
    code, fleet, _ = request("GET", "/api/servers")
    assert code == 200
    assert next(row for row in fleet["servers"] if row["id"] == "beta-2")["error"]["code"] == "INTERNAL"
    assert next(row for row in fleet["servers"] if row["id"] == "alpha-1")["status"] == "ok"


def test_c5_rescan_always_answers_when_actor_busy(collector_api):
    host, request, origin = collector_api
    auth = rescan_headers(request, origin)
    entered, release = threading.Event(), threading.Event()

    def gate(server):
        if server.id == "alpha-1":
            entered.set()
            assert release.wait(10), "observe barrier was never released"

    host.host.observe_gate = gate
    host.fleet.start()
    try:
        assert entered.wait(5), "alpha's actor never started its observe"
        host.fleet.reply_timeout = 0
        try:
            code, body, headers = request("POST", "/api/servers/alpha-1/rescan", "{}", auth)
        except http.client.RemoteDisconnected:
            raise AssertionError("C5 busy actor disconnected the rescan POST without an HTTP response") from None
        assert code == 503, f"C5 busy actor answered {code} {body}"
        assert body["error"] == "SERVER_BUSY" and headers["Retry-After"] == "5"
        host.fleet.reply_timeout = 5
        code, body, _ = request("POST", "/api/servers/beta-2/rescan", "{}", auth)
        assert code == 202 and body["job"]["state"] == "running", "a busy actor blocked another server's rescan"
        code, body, _ = request("POST", "/api/servers/beta-2/rescan", "{}", auth)
        assert code == 409 and body["error"] == "ACTIVE_JOB" and body["job"]["id"]
    finally:
        release.set()


def test_rescan_limits_answer_429(collector_api):
    host, request, origin = collector_api
    auth = rescan_headers(request, origin)
    host.restart(max_active_jobs=1)
    host.use_fleet()
    host.fleet.start()
    code, _, _ = request("POST", "/api/servers/alpha-1/rescan", "{}", auth)
    assert code == 202
    code, body, _ = request("POST", "/api/servers/beta-2/rescan", "{}", auth)
    assert (code, body["error"]) == (429, "GLOBAL_CONCURRENCY")


def test_c6_store_error_on_known_server_is_503(collector_api):
    host, request, _ = collector_api
    (host.state_dir / "servers" / "alpha-1" / "record.json").write_text("{")
    host.restart()
    host.use_fleet()
    code, body, _ = request("GET", "/api/servers/alpha-1/snapshot")
    assert code == 503, f"C6 known server with unreadable store returned {code} {body}"
    assert body["error"] == "STORE_UNAVAILABLE"
    code, body, _ = request("GET", "/api/servers/not-in-inventory/snapshot")
    assert (code, body["error"]) == (404, "UNKNOWN_SERVER")


def test_c9_fleet_responsive_during_large_commit(collector_api, monkeypatch):
    host, request, _ = collector_api
    host.host.publish("alpha-1", started=1719203000)
    actor = host.actor("alpha-1")
    entered, release = threading.Event(), threading.Event()
    commit = actor.store.commit

    def slow_commit(record, capture=None):
        if capture is not None:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("commit barrier was never released")
        return commit(record, capture)

    monkeypatch.setattr(actor.store, "commit", slow_commit)
    host.clock.now += 61
    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(lambda: (actor.step(), actor.step()))
        try:
            assert entered.wait(5), "writer never reached the capture commit"
            code, fleet, _ = request("GET", "/api/servers")
            assert code == 200, "C9 fleet read failed during a commit"
            row = next(row for row in fleet["servers"] if row["id"] == "alpha-1")
            assert row["overview_snapshot"]["scan_generation"] == "alpha-1-1719200000-v1"
        finally:
            release.set()
            writer.result(timeout=10)
    code, fleet, _ = request("GET", "/api/servers")
    assert next(row for row in fleet["servers"] if row["id"] == "alpha-1")["overview_snapshot"]["scan_generation"] == "alpha-1-1719203000-v1"


def test_an_unexpected_admission_failure_is_500_internal(collector_api, monkeypatch):
    # M2b review: the fleet mapped the actor's INTERNAL reply to 503 STORE_UNAVAILABLE (section 4.3).
    from collector import jobs

    host, request, origin = collector_api
    auth = rescan_headers(request, origin)

    def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(jobs, "admit", broken)
    host.fleet.start()
    code, body, _ = request("POST", "/api/servers/alpha-1/rescan", "{}", auth)
    assert (code, body["error"]) == (500, "INTERNAL")
    assert host.summary()["active_job"] is None


def test_v2_host_keeps_the_v1_route_shapes_with_one_mount_per_volume(collector_api):
    from collector.test_volumes import BA, BB, VA, VB, V2Host, capture

    host, request, _ = collector_api
    code, before, _ = request("GET", "/api/servers")
    v1_row = next(row for row in before["servers"] if row["id"] == "alpha-1")
    v2 = V2Host(host, "alpha-1")
    finished = host.clock.now - 600
    v2.volume(VA, BA, "/a", capture(VA, BA, "/a", finished))
    v2.volume(VB, BB, "/b", capture(VB, BB, "/b", finished), media="ssd", cadence=3600)
    v2.publish()
    host.tick()
    code, fleet, _ = request("GET", "/api/servers")
    row = next(row for row in fleet["servers"] if row["id"] == "alpha-1")
    assert code == 200 and set(row) == set(v1_row), "the v1 fleet row changed shape for a v2 host"
    assert (row["snapshot_availability"], row["latest_pull_status"], row["mount_count"]) == ("available", "succeeded", 2)
    code, snapshot, _ = request("GET", "/api/servers/alpha-1/snapshot")
    assert code == 200
    assert {(mount["mount_id"], mount["path"], mount["df_total"], mount["severity"]) for mount in snapshot["mounts"]} == {
        (VA, "/a", 1000, "critical"), (VB, "/b", 1000, "critical")}  # 600 free bytes is under the free floor
    assert snapshot["hostname"] == "alpha-1" and snapshot["mount_table"]["status"] == "ok"
    code, status, _ = request("GET", "/api/servers/alpha-1/scan-status")
    assert code == 200 and status["snapshot_generation"] == snapshot["scan_generation"] and status["published_generation"] is None


def test_v2_rescan_without_request_id_delivery_answers_503(collector_api):
    from collector.test_volumes import BA, VA, V2Host, capture

    host, request, origin = collector_api
    auth = rescan_headers(request, origin)
    v2 = V2Host(host, "alpha-1")
    v2.volume(VA, BA, "/a", capture(VA, BA, "/a", host.clock.now - 600))
    v2.publish()
    host.tick()
    host.host.delivers_request_ids = False
    host.fleet.start()
    code, body, _ = request("POST", "/api/servers/alpha-1/rescan", "{}", auth)
    assert (code, body["error"]) == (503, "RESCAN_UNAVAILABLE")
