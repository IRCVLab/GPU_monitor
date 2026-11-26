#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import re
import json
import select
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path

from collector.test_bug_harness import Collector, payload_for, status_data

ROOT = Path(__file__).resolve().parents[1]
SERVE = ROOT / "viewer" / "serve.py"
GiB = 1024 ** 3


def free_port() -> int:
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close(); return port


def wait_until_serving(proc, timeout=10):
    """Block until serve.py prints its ready line, or fail with its output if it exits first."""
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    line = proc.stdout.readline() if ready else ""
    if not line.startswith("storage-viz serving"):
        proc.kill()
        out, err = proc.communicate(timeout=5)
        raise AssertionError(f"serve.py did not become ready\nstdout={line}{out}\nstderr={err}")


def sample_snapshot(server_id="hinton") -> dict:
    return {
        "schema_version": 1,
        "server_id": server_id,
        "scan_generation": f"{server_id}-1719200000-v1",
        "hostname": server_id,
        "scan_started_unix": 1719200000,
        "scan_finished_unix": 1719200042,
        "config_digest": "a" * 64,
        "selected_roots": [{"mount_id":"root","status":"complete","scan_root":"/","mountpoint":"/","scanned_bytes": 100}],
        "mounts": [{"mount_id":"root","path":"/","scan_root":"/","df_use_pct":50,"df_avail":100*GiB,"tree":{"name":"/","bytes":1,"children":[]}}],
        "top_files": [], "stale": [], "blocked": [],
    }


class CentralPollingLifecycleTest(unittest.TestCase):
    def test_capacity_polling_starts_once_and_stops_cleanly(self):
        from api import server as serve
        polled = threading.Event()
        poller = serve.CentralPoller(polled.set, 3600)
        self.assertTrue(poller.start())
        self.assertTrue(polled.wait(2), "the capacity feed must be polled right after startup")
        first_thread = poller.thread_ident
        self.assertFalse(poller.start(), "second start must not create duplicate polling thread")
        self.assertEqual(poller.thread_ident, first_thread)
        poller.stop(timeout=2)
        self.assertFalse(poller.is_running)

    def test_dev_sample_mode_serves_views_and_refuses_rescans(self):
        from collector.errors import RescanDisabled, UnknownServer
        from api import server as serve
        with tempfile.TemporaryDirectory(prefix="storage-viz-dev-service.") as tmp:
            sample_dir = Path(tmp)
            rows = [{"id":"hinton", "label":"hinton", "file":"hinton", "default":True, "sample_data":True}]
            (sample_dir / "hosts.json").write_text(json.dumps(rows) + "\n", encoding="utf-8")
            (sample_dir / "hinton.json").write_text(json.dumps(sample_snapshot("hinton")) + "\n", encoding="utf-8")
            samples = serve.FixtureFleet(str(sample_dir))
            self.assertEqual(samples.view("hinton").snapshot.overview["scan_generation"], "hinton-1719200000-v1")
            with self.assertRaises(RescanDisabled):
                samples.request_rescan("hinton", "operator")
            with self.assertRaises(UnknownServer):
                samples.view("absent")

    def test_dev_sample_rejects_symlink_root_before_resolve(self):
        from api import server as serve
        with tempfile.TemporaryDirectory(prefix="storage-viz-dev-real.") as real, tempfile.TemporaryDirectory(prefix="storage-viz-dev-link-parent.") as parent:
            real_dir = Path(real)
            rows = [{"id":"hinton", "label":"hinton", "file":"hinton", "default":True, "sample_data":True}]
            (real_dir / "hosts.json").write_text(json.dumps(rows) + "\n", encoding="utf-8")
            (real_dir / "hinton.json").write_text(json.dumps(sample_snapshot("hinton")) + "\n", encoding="utf-8")
            link = Path(parent) / "samples-link"
            os.symlink(real_dir, link)
            with self.assertRaises(ValueError):
                serve.FixtureFleet(str(link))

    def test_import_ignores_unittest_argv_port_but_script_positional_port_still_works(self):
        code = "import sys; sys.argv=['unittest','api.test_auth']; from api.server import main; print(main.__name__)"
        result = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "main")

class SnapshotRouteTest(unittest.TestCase):
    def setUp(self):
        from collector.live_capacity import LiveCapacity
        from api import server as serve

        self.runtime = serve.Runtime()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.host = Collector(Path(temporary.name), "hinton", publish=False)
        self.server_entry = self.host.servers[0]
        self.capacity = LiveCapacity(self.host.servers, 8001, clock=self.host.clock.time)
        self.runtime.fleet = self.host.fleet
        self.runtime.live_capacity = self.capacity
        overrides = patch.object(serve.auth, "TRUSTED_PROXY", True)
        overrides.start()
        self.addCleanup(overrides.stop)
        self.server = serve.Server(("127.0.0.1", 0), self.runtime)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def publish(self, started=1719200000, payload=None):
        payload = payload or payload_for("hinton", started=started)
        for row in (payload["selected_roots"][0], payload["mounts"][0]):
            row.update(capacity_id="dev-8-1", storage_media="ssd", storage_media_confidence="resolved")
        status, data = status_data(payload)
        self.host.host.publications["hinton"], self.host.host.captures[status["sha256"]] = status, data
        self.host.tick()
        payload["mounts"][0].update(used_pct=70, severity="critical")
        return payload

    def get(self, server_id="hinton", *, authenticated=True):
        return self.get_path(f"/api/servers/{server_id}/snapshot", authenticated=authenticated)

    def get_path(self, path, *, authenticated=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=4)
        try:
            headers = {"X-Forwarded-User": "test-viewer"} if authenticated else {}
            connection.request("GET", path, headers=headers)
            with connection.getresponse() as response:
                return response.status, json.load(response)
        finally:
            connection.close()

    def test_all_v1_capacity_views_use_inventory_after_live_overlay(self):
        from collector.inventory import CapacityThresholds
        from collector.live_capacity import CapacitySample, VolumeCapacity

        payload = payload_for("hinton")
        started = payload["scan_started_unix"]
        file_row = dict(path="/home/a", kind="file", bytes=20, uid=1000, owner="user", mtime=started - 86400)
        payload["top_files"] = [file_row]
        payload["stale"] = [dict(file_row, age_days=999)]
        self.publish(payload=payload)
        key = ("dev-8-1", "/dev/storage-viz/home", "ext4")
        self.capacity._samples[(self.server_entry.host, self.server_entry.port)] = CapacitySample(
            self.host.clock.now, {key: VolumeCapacity(100, 75, 25, 75)},
        )
        policy = CapacityThresholds(warning_used_pct=75, warning_free_bytes=10, critical_free_bytes=1)
        with patch.object(self.runtime, "capacity_thresholds", policy):
            detail = self.get()[1]
            summary = self.get_path("/api/servers")[1]["servers"][0]["overview_snapshot"]
            scan = self.get_path("/api/servers/hinton/scan-status")[1]["capacity"]
        for view in (detail, summary, scan):
            self.assertEqual(view["mounts"][0]["used_pct"], 75)
            self.assertEqual(view["mounts"][0]["severity"], "warning")
        self.assertEqual(detail["top_files"][0]["age_days"], 1)
        self.assertEqual(detail["stale"][0]["age_days"], 1)
        stored = json.loads(self.host.actor("hinton").store.read_capture("legacy", self.host.fleet.view("hinton").data.accepted["legacy"]))
        self.assertEqual(stored["stale"][0]["age_days"], 999)
        self.assertNotIn("severity", stored["mounts"][0])

    def test_parallel_detail_reads_never_touch_the_store(self):
        expected = self.publish()
        store = self.host.actor("hinton").store
        with patch.object(store, "read_capture", side_effect=AssertionError("a read went to disk")), ThreadPoolExecutor(max_workers=6) as pool:
            reads = list(pool.map(lambda _: self.get(), range(12)))
        self.assertEqual(reads, [(200, expected)] * 12)
        newer = self.publish(started=1719200100)
        self.assertEqual(self.get(), (200, newer))

    def test_detail_updates_when_live_capacity_changes_or_expires(self):
        from collector.live_capacity import CapacitySample, MAX_AGE_SECONDS, VolumeCapacity

        expected = self.publish()
        mount = expected["mounts"][0]
        key = ("dev-8-1", "/dev/storage-viz/home", "ext4")
        target = (self.server_entry.host, self.server_entry.port)
        self.assertEqual(self.get(authenticated=False), (401, {"authenticated": False, "error": "AUTH_REQUIRED"}))
        self.assertEqual(self.get()[1]["mounts"][0], mount)
        at = self.host.clock.now
        self.capacity._samples = {target: CapacitySample(at, {key: VolumeCapacity(100, 80, 20, 80)})}
        self.assertEqual(self.get()[1]["mounts"][0]["df_used"], 80)
        self.capacity._samples = {target: CapacitySample(at, {key: VolumeCapacity(100, 81, 19, 81)})}
        self.assertEqual(self.get()[1]["mounts"][0]["df_used"], 81)
        self.host.clock.now += MAX_AGE_SECONDS + 1
        self.assertEqual(self.get()[1]["mounts"][0], mount)

    def test_absent_snapshot_can_be_published_after_a_404(self):
        self.assertEqual(self.get(), (404, {"error": "SNAPSHOT_ABSENT"}))
        self.assertEqual(self.get("absent"), (404, {"error": "UNKNOWN_SERVER"}))
        expected = self.publish()
        self.assertEqual(self.get(), (200, expected))


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="storage-viz-api-test."); self.addCleanup(self.tmp.cleanup)
        self.sample_dir = Path(self.tmp.name) / "samples"; self.sample_dir.mkdir()
        self.write_manifest(["atlas", "hinton"])
        (self.sample_dir / "hinton.json").write_text(json.dumps(sample_snapshot("hinton")) + "\n", encoding="utf-8")
        (self.sample_dir / "atlas.json").write_text(json.dumps(sample_snapshot("atlas")) + "\n", encoding="utf-8")
        self.proc = None; self.addCleanup(self._stop)

    def start_server(self, extra_env=None, expect_exit=False, *, fixtures=None):
        self.port = free_port()
        env = os.environ.copy(); env.update(STORAGE_VIZ_BIND="127.0.0.1", STORAGE_VIZ_PORT=str(self.port))
        if extra_env: env.update(extra_env)
        if fixtures is None:
            fixtures = not env.get("STORAGE_VIZ_INVENTORY")
        args = ["--fixtures", str(self.sample_dir)] if fixtures else []
        self.proc = subprocess.Popen([sys.executable, str(SERVE), *args], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if expect_exit:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.fail("serve.py accepted a configuration it must refuse")
            return
        wait_until_serving(self.proc)

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.communicate(timeout=3)
        if self.proc:
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream and not stream.closed:
                    stream.close()


    def write_manifest(self, ids):
        rows = []
        for i, sid in enumerate(ids):
            rows.append({"id": sid, "label": sid.title(), "file": sid, "default": i == 0, "sample_data": True})
        (self.sample_dir / "hosts.json").write_text(json.dumps(rows) + "\n", encoding="utf-8")

    def write_inventory(self):
        inv = Path(self.tmp.name) / "servers.json"
        inv.write_text(json.dumps({"servers":[{"id":"hinton","display_name":"hinton","order":1,"host":"hinton.example.test","port":22,"enabled":True,"username":"monitoring","identity_file":"/etc/storage-viz/hinton.key","known_hosts_file":"/etc/storage-viz/known_hosts","scanner":{"server_id":"hinton"}}]}) + "\n", encoding="utf-8")
        return inv

    def request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type":"application/json"} if body is not None else {}
        return self.request_raw(method, path, data, headers={**h, **(headers or {})})

    def request_raw(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        conn.request(method, path, body=data, headers=headers or {})
        res = conn.getresponse(); raw = res.read(); hdrs = dict(res.getheaders()); conn.close()
        parsed = json.loads(raw.decode() or "{}") if raw else {}
        return res.status, hdrs, parsed

    def cookie_value(self, headers):
        cookie = headers.get("Set-Cookie", "")
        m = re.search(r"storage_viz_session=([^;]+)", cookie)
        self.assertIsNotNone(m, cookie)
        return "storage_viz_session=" + m.group(1)


class ApiServerTest(ServerCase):
    def test_scan_status_read_endpoint_and_unknown_server(self):
        self.start_server()
        code, _, status = self.request("GET", "/api/servers/hinton/scan-status")
        self.assertEqual(code, 200)
        self.assertEqual(status["state"], "unknown")
        self.assertEqual(status["last_finished_unix"], 1719200042)
        self.assertFalse(status["progress_available"])
        self.assertIsNone(status["next_run_unix"])
        self.assertEqual(self.request("GET", "/api/servers/unknown/scan-status")[0], 404)

    def test_dev_sample_api_is_read_only_and_ai_routes_404(self):
        self.start_server()
        code, headers, session = self.request("GET", "/api/session")
        self.assertEqual(code, 200)
        self.assertFalse(session["can_rescan"])
        self.assertIn("SameSite=Lax", headers.get("Set-Cookie", ""))
        code, _, servers = self.request("GET", "/api/servers")
        self.assertEqual(code, 200)
        self.assertEqual(servers["data_mode"], "sample")
        self.assertEqual([s["id"] for s in servers["servers"]], ["atlas", "hinton"])
        self.assertEqual(self.request("GET", "/api/servers/hinton/snapshot")[2]["server_id"], "hinton")
        self.assertEqual(self.request("GET", "/api/servers/unknown/snapshot")[0], 404)
        self.assertEqual(self.request("POST", "/api/servers/hinton/rescan", {})[0], 403)
        self.assertEqual(self.request("GET", "/ai/status")[0], 404)
        self.assertEqual(self.request("POST", "/ai/recommend", {})[0], 404)
        self.assertEqual(self.request("GET", "/capabilities")[2]["rescan"], False)
        self.assertEqual(self.request("POST", "/rescan", {})[0], 503)

    def test_direct_mode_with_inventory_still_disables_rescan(self):
        inv = self.write_inventory()
        self.start_server({"STORAGE_VIZ_INVENTORY":str(inv), "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state")})
        code, _, sess = self.request("GET", "/api/session")
        self.assertEqual(code, 200); self.assertFalse(sess["can_rescan"])
        code, _, servers = self.request("GET", "/api/servers")
        self.assertEqual(code, 200); self.assertEqual(servers["data_mode"], "inventory")
        self.assertEqual(self.request("POST", "/api/servers/hinton/rescan", {}, headers={"Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 403)

    def test_direct_loopback_rescan_opt_in_allows_only_the_ssh_tunnel_origin(self):
        inv = self.write_inventory()
        self.port = free_port()
        origin = f"http://127.0.0.1:{self.port}"
        localhost_origin = f"http://localhost:{self.port}"
        env = os.environ.copy()
        env.update(
            STORAGE_VIZ_BIND="127.0.0.1",
            STORAGE_VIZ_PORT=str(self.port),
            STORAGE_VIZ_INVENTORY=str(inv),
            STORAGE_VIZ_STATE_DIR=str(Path(self.tmp.name) / "state"),
            STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN="1",
            STORAGE_VIZ_ALLOWED_ORIGINS=f"{origin},{localhost_origin}",
            STORAGE_VIZ_OPERATOR_ALLOWLIST="direct-viewer",
        )
        self.proc = subprocess.Popen([sys.executable, str(SERVE)], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_until_serving(self.proc)
        code, headers, sess = self.request("GET", "/api/session")
        self.assertEqual(code, 200)
        self.assertTrue(sess["can_rescan"])
        cookie = self.cookie_value(headers)
        valid_headers = {"Cookie":cookie, "Origin":origin, "X-CSRF-Token":sess["csrf_token"]}
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers=valid_headers)[0], 404)
        mismatch_headers = {**valid_headers, "Origin":localhost_origin}
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers=mismatch_headers)[0], 403)
        lan_headers = {**valid_headers, "Host":"192.168.0.3:8088", "Origin":"http://192.168.0.3:8088"}
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers=lan_headers)[0], 403)
        self.assertFalse(self.request("GET", "/api/session", headers={"Host":"192.168.0.3:8088"})[2]["can_rescan"])
        proxied_loopback_headers = {"Host":f"127.0.0.1:{self.port}", "X-Forwarded-For":"192.168.0.20"}
        self.assertFalse(self.request("GET", "/api/session", headers=proxied_loopback_headers)[2]["can_rescan"])

    def test_direct_loopback_rescan_rejects_unsafe_startup_configuration(self):
        inv = self.write_inventory()
        base = {
            "STORAGE_VIZ_INVENTORY":str(inv),
            "STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN":"1",
            "STORAGE_VIZ_OPERATOR_ALLOWLIST":"direct-viewer",
            "STORAGE_VIZ_ALLOWED_ORIGINS":"http://127.0.0.1:8088",
        }
        for overrides in (
            {"STORAGE_VIZ_BIND":"0.0.0.0"},
            {"STORAGE_VIZ_ALLOWED_ORIGINS":""},
            {"STORAGE_VIZ_ALLOWED_ORIGINS":"http://127.0.0.1:notaport"},
            {"STORAGE_VIZ_OPERATOR_ALLOWLIST":"operator-1"},
            {"STORAGE_VIZ_TRUSTED_PROXY":"1"},
        ):
            with self.subTest(overrides=overrides):
                self.start_server({**base, **overrides}, expect_exit=True)
                self._stop(); self.proc = None
        self.start_server({
            "STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN":"1",
            "STORAGE_VIZ_OPERATOR_ALLOWLIST":"direct-viewer",
            "STORAGE_VIZ_ALLOWED_ORIGINS":"http://127.0.0.1:8088",
        }, expect_exit=True)

    def test_trusted_proxy_requires_loopback_exact_origin_operator_and_csrf(self):
        inv = self.write_inventory()
        self.start_server({"STORAGE_VIZ_INVENTORY":str(inv), "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"), "STORAGE_VIZ_TRUSTED_PROXY":"1", "STORAGE_VIZ_ALLOWED_ORIGINS":"http://storage.test", "STORAGE_VIZ_OPERATOR_ALLOWLIST":"operator-1"})
        self.assertEqual(self.request("GET", "/api/session")[0], 401)
        self.assertEqual(self.request("GET", "/api/servers")[0], 401)
        self.assertEqual(self.request("GET", "/api/servers/hinton/scan-status")[0], 401)
        self.assertEqual(self.request("GET", "/data/hinton.json")[0], 404)
        code, headers, sess = self.request("GET", "/api/session", headers={"X-Forwarded-User":"viewer-1"})
        self.assertEqual(code, 200); self.assertFalse(sess["can_rescan"])
        viewer_cookie = self.cookie_value(headers)
        self.assertIn("SameSite=Strict", headers.get("Set-Cookie", ""))
        self.assertIn("Secure", headers.get("Set-Cookie", ""))
        code, _, servers = self.request("GET", "/api/servers", headers={"X-Forwarded-User":"viewer-1"})
        self.assertEqual(code, 200); self.assertEqual(servers["data_mode"], "inventory")
        self.assertEqual(self.request("GET", "/data/hinton.json", headers={"X-Forwarded-User":"viewer-1"})[0], 404)
        self.assertEqual(self.request("POST", "/api/servers/hinton/rescan", {}, headers={"Cookie":viewer_cookie, "X-Forwarded-User":"viewer-1", "Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 403)
        code, headers, sess = self.request("GET", "/api/session", headers={"X-Forwarded-User":"operator-1"})
        self.assertTrue(sess["can_rescan"])
        operator_cookie = self.cookie_value(headers)
        code2, headers2, sess2 = self.request("GET", "/api/session", headers={"Cookie":operator_cookie, "X-Forwarded-User":"operator-1"})
        self.assertEqual(code2, 200); self.assertEqual(sess2["csrf_token"], sess["csrf_token"])
        self.assertNotIn("Set-Cookie", headers2)
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers={"X-Forwarded-User":"operator-1", "Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 403, "POST without Cookie must fail before server lookup")
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers={"Cookie":operator_cookie, "X-Forwarded-User":"viewer-1", "Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 403)
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers={"Cookie":"storage_viz_session=bad", "X-Forwarded-User":"operator-1", "Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 403)
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers={"Cookie":operator_cookie, "X-Forwarded-User":"operator-1", "Origin":"http://evil.test", "X-CSRF-Token":sess["csrf_token"]})[0], 403)
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers={"Cookie":operator_cookie, "X-Forwarded-User":"operator-1", "Origin":"http://storage.test"})[0], 403)
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers={"Cookie":operator_cookie, "X-Forwarded-User":"operator-1", "Origin":"http://storage.test", "X-CSRF-Token":"bad"})[0], 403)
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers={"Cookie":operator_cookie, "X-Forwarded-User":"operator-1", "Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 404)
        self.assertEqual(self.request_raw("POST", "/api/servers/hinton/rescan", b"", headers={"Content-Type":"application/json", "Cookie":operator_cookie, "X-Forwarded-User":"operator-1", "Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 400)
        for raw in (b"[]", b"not-json", b"{}{}", b"{\"command\":\"scan\"}", b"{\"path\":\"/tmp\"}"):
            self.assertEqual(self.request_raw("POST", "/api/servers/hinton/rescan", raw, headers={"Content-Type":"application/json", "Cookie":operator_cookie, "X-Forwarded-User":"operator-1", "Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 400)

    def test_explicit_internal_http_proxy_mode_uses_non_secure_session_cookie_and_keeps_csrf(self):
        inv = self.write_inventory()
        origin = "http://166.104.167.11:505"
        self.start_server({
            "STORAGE_VIZ_INVENTORY":str(inv),
            "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"),
            "STORAGE_VIZ_TRUSTED_PROXY":"1",
            "STORAGE_VIZ_ALLOWED_ORIGINS":origin,
            "STORAGE_VIZ_OPERATOR_ALLOWLIST":"lan-operator",
            "STORAGE_VIZ_SESSION_COOKIE_SECURE":"0",
        })
        code, headers, sess = self.request("GET", "/api/session", headers={"X-Forwarded-User":"lan-operator"})
        self.assertEqual(code, 200)
        self.assertTrue(sess["can_rescan"])
        self.assertIn("SameSite=Strict", headers.get("Set-Cookie", ""))
        self.assertNotIn("Secure", headers.get("Set-Cookie", ""))
        cookie = self.cookie_value(headers)
        post_headers = {
            "Cookie":cookie,
            "X-Forwarded-User":"lan-operator",
            "Origin":origin,
            "X-CSRF-Token":sess["csrf_token"],
        }
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers=post_headers)[0], 404)

    def test_session_cookie_secure_override_rejects_invalid_or_non_proxy_configuration(self):
        inv = self.write_inventory()
        base = {
            "STORAGE_VIZ_INVENTORY":str(inv),
            "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"),
        }
        for overrides in (
            {"STORAGE_VIZ_SESSION_COOKIE_SECURE":"sometimes"},
            {"STORAGE_VIZ_SESSION_COOKIE_SECURE":"0"},
        ):
            with self.subTest(overrides=overrides):
                self.start_server({**base, **overrides}, expect_exit=True)
                self._stop(); self.proc = None

    def test_non_secure_proxy_rejects_non_exact_http_origins(self):
        inv = self.write_inventory()
        base = {
            "STORAGE_VIZ_INVENTORY":str(inv),
            "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"),
            "STORAGE_VIZ_TRUSTED_PROXY":"1",
            "STORAGE_VIZ_OPERATOR_ALLOWLIST":"lan-operator",
            "STORAGE_VIZ_SESSION_COOKIE_SECURE":"0",
        }
        for origin in (
            "",
            "https://storage.test",
            "http://user@storage.test",
            "http://storage.test/path",
            "http://storage.test?query=1",
            "http://storage.test#fragment",
            "http://storage.test:notaport",
            "http://storage.test:",
        ):
            with self.subTest(origin=origin):
                self.start_server({**base, "STORAGE_VIZ_ALLOWED_ORIGINS":origin}, expect_exit=True)
                self._stop(); self.proc = None


    def test_dev_sample_requires_valid_manifest_and_files(self):
        for rows in (
            [],
            [{"id":"bad/slash", "label":"bad", "file":"bad", "default":True, "sample_data":True}],
            [{"id":"dup", "label":"dup", "file":"dup", "default":True, "sample_data":True}, {"id":"dup", "label":"dup2", "file":"dup2", "sample_data":True}],
            [{"id":"a", "label":"a", "file":"hinton", "default":True, "sample_data":True}, {"id":"b", "label":"b", "file":"hinton", "sample_data":True}],
            [{"id":"a", "label":"a", "file":"atlas", "default":True, "sample_data":True}, {"id":"b", "label":"b", "file":"hinton", "default":True, "sample_data":True}],
            [{"id":"missing", "label":"missing", "file":"missing", "default":True, "sample_data":True}],
        ):
            with self.subTest(rows=rows):
                self.write_manifest([])
                (self.sample_dir / "hosts.json").write_text(json.dumps(rows) + "\n", encoding="utf-8")
                self.start_server(expect_exit=True)
                self._stop(); self.proc = None

    def test_dev_sample_rejects_unlisted_sample_and_path_or_symlink_escape(self):
        (self.sample_dir / "orphan.json").write_text(json.dumps(sample_snapshot("orphan")) + "\n", encoding="utf-8")
        self.start_server(expect_exit=True)
        self._stop(); self.proc = None
        (self.sample_dir / "orphan.json").unlink()
        (self.sample_dir / ".hidden.json").write_text(json.dumps(sample_snapshot("hidden")) + "\n", encoding="utf-8")
        self.start_server(expect_exit=True)
        self._stop(); self.proc = None
        (self.sample_dir / ".hidden.json").unlink()
        (self.sample_dir / "atlas.json").unlink()
        (self.sample_dir / "outside.json").write_text(json.dumps(sample_snapshot("outside")) + "\n", encoding="utf-8")
        os.symlink(self.sample_dir / "outside.json", self.sample_dir / "atlas.json")
        self.start_server(expect_exit=True)
        self._stop(); self.proc = None
        (self.sample_dir / "atlas.json").unlink()
        self.write_manifest(["escape"])
        (self.sample_dir / "hosts.json").write_text(json.dumps([{"id":"escape", "label":"escape", "file":"../escape", "default":True, "sample_data":True}]) + "\n", encoding="utf-8")
        self.start_server(expect_exit=True)


    def test_direct_script_positional_port_is_preserved(self):
        self.port = free_port()
        env = os.environ.copy()
        env.update(STORAGE_VIZ_BIND="127.0.0.1")
        env.pop("STORAGE_VIZ_PORT", None)
        self.proc = subprocess.Popen([sys.executable, str(SERVE), str(self.port)], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_until_serving(self.proc)
        self.assertEqual(self.request("GET", "/api/session")[0], 200)

    def test_expired_session_cookie_is_rejected_on_post(self):
        inv = self.write_inventory()
        secret = "s" * 64
        self.start_server({"STORAGE_VIZ_INVENTORY":str(inv), "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"), "STORAGE_VIZ_TRUSTED_PROXY":"1", "STORAGE_VIZ_ALLOWED_ORIGINS":"http://storage.test", "STORAGE_VIZ_OPERATOR_ALLOWLIST":"operator-1", "STORAGE_VIZ_CSRF_SECRET":secret})
        issued = int(time.time()) - 7200
        claims = {"actor": "operator-1", "iat": issued, "exp": issued + 3600, "nonce": "0" * 32}
        encoded = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")
        value = encoded + "." + hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        csrf = hmac.new(secret.encode(), ("csrf:" + value).encode(), hashlib.sha256).hexdigest()
        headers = {"Cookie": "storage_viz_session=" + value, "X-Forwarded-User":"operator-1", "Origin":"http://storage.test", "X-CSRF-Token":csrf}
        code, _, body = self.request("POST", "/api/servers/unknown/rescan", {}, headers=headers)
        self.assertEqual((code, body.get("error")), (403, "BAD_SESSION"))

    def test_operator_mode_without_allowed_origin_fails_startup(self):
        inv = self.write_inventory()
        self.start_server({"STORAGE_VIZ_INVENTORY":str(inv), "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"), "STORAGE_VIZ_TRUSTED_PROXY":"1", "STORAGE_VIZ_OPERATOR_ALLOWLIST":"operator-1"}, expect_exit=True)

    def test_trusted_proxy_fixtures_require_identity(self):
        self.start_server({"STORAGE_VIZ_TRUSTED_PROXY":"1"})
        self.assertEqual(self.request("GET", "/api/servers")[0], 401)
        code, _, body = self.request("GET", "/api/servers", headers={"X-Forwarded-User":"viewer"})
        self.assertEqual((code, body["data_mode"]), (200, "sample"))

    def test_production_refuses_fixtures(self):
        self.start_server({"STORAGE_VIZ_ENV":"production"}, expect_exit=True)

    def test_dev_sample_rejected_when_proxy_mode_not_loopback_or_combined_with_prod(self):
        self.start_server({"STORAGE_VIZ_TRUSTED_PROXY":"1", "STORAGE_VIZ_BIND":"0.0.0.0"}, expect_exit=True)



class CandidatePreflightBackendModeTest(ServerCase):
    def start_preflight(self, overrides=None, expect_exit=False, *, fixtures=False):
        inv = self.write_inventory()
        env = {
            "STORAGE_VIZ_INVENTORY":str(inv),
            "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"),
            "STORAGE_VIZ_PREFLIGHT_BACKEND":"1",
            "STORAGE_VIZ_TRUSTED_PROXY":"1",
            "STORAGE_VIZ_ALLOWED_ORIGINS":"http://166.104.167.11:505",
            "STORAGE_VIZ_OPERATOR_ALLOWLIST":"fixed-proxy-operator",
            "STORAGE_VIZ_SESSION_COOKIE_SECURE":"0",
            "STORAGE_VIZ_BIND":"127.0.0.1",
        }
        if overrides:
            env.update(overrides)
        self.start_server(env, expect_exit=expect_exit, fixtures=fixtures)

    def test_preflight_backend_allows_session_csrf_and_unknown_probe_but_refuses_known_jobs(self):
        self.start_preflight()
        code, headers, sess = self.request("GET", "/api/session", headers={"X-Forwarded-User":"fixed-proxy-operator"})
        self.assertEqual(code, 200)
        self.assertTrue(sess["can_rescan"])
        self.assertNotIn("Secure", headers.get("Set-Cookie", ""))
        cookie = self.cookie_value(headers)
        code, _, servers = self.request("GET", "/api/servers", headers={"X-Forwarded-User":"fixed-proxy-operator"})
        self.assertEqual(code, 200)
        self.assertEqual(servers["data_mode"], "inventory")
        self.assertEqual([s["id"] for s in servers["servers"]], ["hinton"])
        post_headers = {"Cookie":cookie, "X-Forwarded-User":"fixed-proxy-operator", "Origin":"http://166.104.167.11:505", "X-CSRF-Token":sess["csrf_token"]}
        code, _, body = self.request("POST", "/api/servers/absent-preflight/rescan", {}, headers=post_headers)
        self.assertEqual(code, 404)
        self.assertEqual(body.get("error"), "UNKNOWN_SERVER")
        code, _, body = self.request("POST", "/api/servers/hinton/rescan", {}, headers=post_headers)
        self.assertEqual(code, 403)
        self.assertEqual(body.get("error"), "RESCAN_DISABLED")

    def test_preflight_backend_rejects_sample_direct_public_bind_and_missing_inventory(self):
        self.start_preflight(expect_exit=True, fixtures=True)
        self._stop()
        for overrides in (
            {"STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN":"1"},
            {"STORAGE_VIZ_BIND":"0.0.0.0"},
            {"STORAGE_VIZ_INVENTORY":""},
        ):
            with self.subTest(overrides=overrides):
                self.start_preflight(overrides, expect_exit=True)
                self._stop(); self.proc = None

    def test_preflight_backend_does_not_build_central_poller_or_execute_real_jobs(self):
        code = """
import os, pathlib, sys, tempfile, json
root = pathlib.Path(sys.argv[1])
inv = root / 'servers.json'
inv.write_text(json.dumps({'servers':[{'id':'hinton','display_name':'hinton','order':1,'host':'hinton.example.test','port':22,'enabled':True,'username':'monitoring','identity_file':'/etc/storage-viz/hinton.key','known_hosts_file':'/etc/storage-viz/known_hosts','scanner':{'server_id':'hinton'}}]}))
os.environ.update(STORAGE_VIZ_INVENTORY=str(inv), STORAGE_VIZ_STATE_DIR=str(root/'state'), STORAGE_VIZ_PREFLIGHT_BACKEND='1', STORAGE_VIZ_TRUSTED_PROXY='1', STORAGE_VIZ_ALLOWED_ORIGINS='http://166.104.167.11:505', STORAGE_VIZ_OPERATOR_ALLOWLIST='fixed-proxy-operator', STORAGE_VIZ_SESSION_COOKIE_SECURE='0', STORAGE_VIZ_BIND='127.0.0.1')
sys.path.insert(0, str(pathlib.Path('apps/storage-monitor').resolve()))
from api import server as serve
runtime = serve.configure()
print(runtime.runs_actors, runtime.live_capacity, runtime.fleet.actors['hinton'].loaded)
"""
        with tempfile.TemporaryDirectory(prefix="storage-viz-preflight-import.") as tmp:
            result = subprocess.run([sys.executable, "-c", code, tmp], cwd=str(ROOT.parents[1]), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False None False")
