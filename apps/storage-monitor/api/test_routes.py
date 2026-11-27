import http.client
import threading
from concurrent.futures import ThreadPoolExecutor


def rescan_headers(request, origin):
    code, session, headers = request("GET", "/api/v2/session")
    assert code == 200 and session["can_rescan"]
    return {"Origin": origin, "Cookie": headers["Set-Cookie"].split(";", 1)[0], "X-CSRF-Token": session["csrf_token"]}


def test_c3_fleet_isolates_unreadable_server(collector_api, monkeypatch):
    from api import server as serve

    host, request, _ = collector_api
    code, fleet, _ = request("GET", "/api/v2/fleet")
    assert code == 200
    healthy = next(row for row in fleet["servers"] if row["server_id"] == "alpha-1")
    (host.state_dir / "servers" / "beta-2" / "record.json").write_text("{")
    host.restart()
    host.use_fleet()
    host.tick()
    try:
        code, fleet, _ = request("GET", "/api/v2/fleet")
    except http.client.RemoteDisconnected:
        raise AssertionError("C3 unreadable server disconnected the fleet request") from None
    assert code == 200
    alpha = next(row for row in fleet["servers"] if row["server_id"] == "alpha-1")
    volatile = {"activity", "link"}  # the observe after the restart is a newer contact
    assert {k: v for k, v in alpha.items() if k not in volatile} == {k: v for k, v in healthy.items() if k not in volatile}
    assert alpha["link"]["state"] == healthy["link"]["state"] == "ok"
    broken = next(row for row in fleet["servers"] if row.get("id", row.get("server_id")) == "beta-2")
    assert broken["error"]["code"] == "STORE_UNAVAILABLE", "C3 broken server row is not a typed error"

    render = serve.routes.server_bytes
    monkeypatch.setattr(serve.routes, "server_bytes", lambda view, now, thresholds: (_ for _ in ()).throw(RuntimeError("bug")) if view.server.id == "beta-2" else render(view, now, thresholds))
    code, fleet, _ = request("GET", "/api/v2/fleet")
    assert code == 200
    assert next(row for row in fleet["servers"] if row["server_id"] == "beta-2")["error"]["code"] == "INTERNAL"
    assert next(row for row in fleet["servers"] if row["server_id"] == "alpha-1")["status"] == "ok"


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
            code, body, headers = request("POST", "/api/v2/servers/alpha-1/rescan", "{}", auth)
        except http.client.RemoteDisconnected:
            raise AssertionError("C5 busy actor disconnected the rescan POST without an HTTP response") from None
        assert code == 503, f"C5 busy actor answered {code} {body}"
        assert body["error"]["code"] == "SERVER_BUSY" and headers["Retry-After"] == "5"
        host.fleet.reply_timeout = 5
        code, body, _ = request("POST", "/api/v2/servers/beta-2/rescan", "{}", auth)
        assert code == 202 and body["job"]["state"] == "dispatching", "a busy actor blocked another server's rescan"
        code, body, _ = request("POST", "/api/v2/servers/beta-2/rescan", "{}", auth)
        assert code == 409 and body["error"]["code"] == "ACTIVE_JOB" and body["error"]["job"]["request_id"]
    finally:
        release.set()


def test_rescan_limits_answer_429(collector_api):
    host, request, origin = collector_api
    auth = rescan_headers(request, origin)
    host.restart(max_active_jobs=1)
    host.use_fleet()
    host.fleet.start()
    code, _, _ = request("POST", "/api/v2/servers/alpha-1/rescan", "{}", auth)
    assert code == 202
    code, body, _ = request("POST", "/api/v2/servers/beta-2/rescan", "{}", auth)
    assert (code, body["error"]["code"]) == (429, "GLOBAL_CONCURRENCY")


def test_c6_store_error_on_known_server_is_503(collector_api):
    host, request, _ = collector_api
    (host.state_dir / "servers" / "alpha-1" / "record.json").write_text("{")
    host.restart()
    host.use_fleet()
    code, body, _ = request("GET", "/api/v2/servers/alpha-1")
    assert code == 503, f"C6 known server with unreadable store returned {code} {body}"
    assert body["error"]["code"] == "STORE_UNAVAILABLE"
    code, body, _ = request("GET", "/api/v2/servers/not-in-inventory")
    assert (code, body["error"]["code"]) == (404, "UNKNOWN_SERVER")


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
            code, fleet, _ = request("GET", "/api/v2/fleet")
            assert code == 200, "C9 fleet read failed during a commit"
            row = next(row for row in fleet["servers"] if row["server_id"] == "alpha-1")
            assert row["volumes"][0]["capture"]["scan_finished_unix"] == 1719200042
        finally:
            release.set()
            writer.result(timeout=10)
    code, fleet, _ = request("GET", "/api/v2/fleet")
    assert next(row for row in fleet["servers"] if row["server_id"] == "alpha-1")["volumes"][0]["capture"]["scan_finished_unix"] == 1719203042


def test_an_unexpected_admission_failure_is_500_internal(collector_api, monkeypatch):
    # M2b review: the fleet mapped the actor's INTERNAL reply to 503 STORE_UNAVAILABLE (section 4.3).
    from collector import jobs

    host, request, origin = collector_api
    auth = rescan_headers(request, origin)

    def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(jobs, "admit", broken)
    host.fleet.start()
    code, body, _ = request("POST", "/api/v2/servers/alpha-1/rescan", "{}", auth)
    assert (code, body["error"]["code"]) == (500, "INTERNAL")
    assert host.latest_job() is None


def test_v2_rescan_without_request_id_delivery_answers_503(collector_api):
    from collector.test_volumes import BA, VA, V2Host, capture

    host, request, origin = collector_api
    auth = rescan_headers(request, origin)
    v2 = V2Host(host, "alpha-1")
    v2.volume(VA, BA, "/a", capture(VA, BA, "/a", host.clock.now - 600))
    v2.publish()
    host.host.delivers_request_ids = False  # observed over S2
    host.tick()
    host.fleet.start()
    code, body, _ = request("POST", "/api/v2/servers/alpha-1/rescan", "{}", auth)
    assert (code, body["error"]["code"]) == (503, "RESCAN_UNAVAILABLE")


def test_servers_show_each_transport_the_agent_update_track_and_the_rollout(collector_api, tmp_path):
    import dataclasses

    from collector.test_bug_harness import FakeUpdater
    from collector.test_rollout import write_agent

    host, request, _ = collector_api
    host.servers = [dataclasses.replace(host.servers[0], canary=True), dataclasses.replace(host.servers[1], transport="legacy")]
    host.host.agents["alpha-1"] = FakeUpdater("1" * 40)
    write_agent(tmp_path / "agent", "2" * 40)
    host.restart(desired_path=tmp_path / "agent" / "storage-viz-agent.json")
    host.use_fleet()
    host.tick()  # stage and activate
    host.tick()  # the gate reports the new release
    code, fleet, _ = request("GET", "/api/v2/fleet")
    assert code == 200
    rows = {row["server_id"]: row for row in fleet["servers"]}
    assert rows["alpha-1"]["link"]["transport"] == "gate" and rows["alpha-1"]["link"]["state"] == "ok"
    assert rows["alpha-1"]["agent"] == {"release_sha": "2" * 40, "matches_desired": True, "update": "probation", "last_error": None}
    assert rows["beta-2"]["agent"]["update"] == "in_sync" and rows["beta-2"]["agent"]["release_sha"] is None
    assert fleet["rollout"] == {"phase": "canary", "reason": None, "desired_sha": "2" * 40, "release_seq": 7,
                                "canary": "alpha-1", "passed": []}


def test_capture_contract_and_retention(collector_api):
    from collector.test_volumes import VA, BA, VB, BB, SID, V2Host, capture, sha
    host, request, _ = collector_api
    agent = V2Host(host)
    finished = host.clock.now
    first = agent.volume(VA, BA, "/a", capture(VA, BA, "/a", finished))
    second = agent.volume(VB, BB, "/b", capture(VB, BB, "/b", finished - 2 * 86400))
    agent.publish()
    host.tick()
    base = f"/api/v2/servers/{SID}"
    url = f"{base}/volumes/{VA}/captures/{sha(first)}"
    code, raw, headers = request("GET", url)
    assert code == 200 and raw == first
    assert headers["Content-Length"] == str(len(first))
    assert headers["ETag"] == f'"{sha(first)}"'
    assert headers["Content-Encoding"] == "gzip" and headers["X-Request-Id"]
    assert headers.get_all("Cache-Control") == ["private, max-age=31536000, immutable"]
    code, detail, _ = request("GET", base)
    assert code == 200 and detail["host"] == {"hostname": SID, "mount_table": host.actor().view.data.manifest.mount_table}
    fleet_rows = request("GET", "/api/v2/fleet")[1]["servers"]
    summary = next(row for row in fleet_rows if row["server_id"] == SID)
    assert summary["volumes"][0]["roots"] == detail["volumes"][0]["roots"]
    assert [(r["scan_root"], r["binding"], r["bytes"]) for r in summary["volumes"][0]["roots"]] == [("/a", BA, 30)]
    assert set(detail["users"][0]["by_volume"]) == {VA, VB}
    for key in ("top_files", "stale"):
        rows = {r["volume_id"]:r for r in detail[key]}
        assert rows[VA]["age_days"] == rows[VB]["age_days"] + 2
        assert (rows[VA]["binding"], rows[VB]["binding"]) == (BA, BB)
    for path, status, error in ((url.replace(VA, "v-"+"f"*16), 404, "UNKNOWN_VOLUME"),
                                (url.replace(sha(first), sha(second)), 410, "CAPTURE_GONE"),
                                (url.replace(sha(first), "bad"), 400, "BAD_REQUEST"),
                                (base+"?unexpected=1", 400, "BAD_REQUEST")):
        code, body, headers = request("GET", path)
        assert (code, body["error"]["code"], body["error"]["request_id"]) == (status, error, headers["X-Request-Id"])
    agent.volume(VA, BA, "/a", capture(VA, BA, "/a", finished+10))
    agent.publish()
    host.tick()
    assert request("GET",url)[0] == 410


def test_installed_health_probe_has_no_rescan_side_effect(collector_api):
    host, request, origin = collector_api
    status, session, headers = request("GET","/api/session")
    assert status == 200 and session["can_rescan"]
    auth = {"Cookie":headers["Set-Cookie"].split(";",1)[0], "Origin":origin, "X-CSRF-Token":session["csrf_token"]}
    assert request("GET","/api/servers")[1] == {"data_mode":"inventory", "servers":[{"id":s.id} for s in host.servers]}
    assert request("POST","/api/servers/hc-"+"a"*24+"/rescan","{}",auth)[:2] == (404, {"error":"UNKNOWN_SERVER"})
    assert request("POST","/api/servers/alpha-1/rescan","{}",auth)[0] == 404
