#!/usr/bin/env python3
from __future__ import annotations

import http.client
import re
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVE = ROOT / "viewer" / "serve.py"
GiB = 1024 ** 3


def free_port() -> int:
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close(); return port


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
    class FakePollService:
        poll_interval_seconds = 3600
        def __init__(self):
            self.calls = 0
            self.first = threading.Event()
        def poll_once(self):
            self.calls += 1
            self.first.set()
            return {}

    def test_central_polling_starts_once_and_stops_cleanly(self):
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT))
        from viewer import serve
        svc = self.FakePollService()
        poller = serve.CentralPoller(svc, interval_seconds=3600)
        self.assertTrue(poller.start())
        self.assertTrue(svc.first.wait(2), "production inventory mode must poll immediately after startup")
        first_thread = poller.thread_ident
        self.assertFalse(poller.start(), "second start must not create duplicate polling thread")
        self.assertEqual(poller.thread_ident, first_thread)
        poller.stop(timeout=2)
        self.assertFalse(poller.is_running)

    def test_dev_sample_mode_does_not_create_poller(self):
        from viewer import serve
        with tempfile.TemporaryDirectory(prefix="storage-viz-dev-service.") as tmp:
            sample_dir = Path(tmp)
            rows = [{"id":"hinton", "label":"hinton", "file":"hinton", "default":True, "sample_data":True}]
            (sample_dir / "hosts.json").write_text(json.dumps(rows) + "\n", encoding="utf-8")
            (sample_dir / "hinton.sample.json").write_text(json.dumps(sample_snapshot("hinton")) + "\n", encoding="utf-8")
            self.assertIsNone(serve.build_central_poller(serve._DevSampleService(str(sample_dir))))



    def test_dev_sample_rejects_symlink_root_before_resolve(self):
        from viewer import serve
        with tempfile.TemporaryDirectory(prefix="storage-viz-dev-real.") as real, tempfile.TemporaryDirectory(prefix="storage-viz-dev-link-parent.") as parent:
            real_dir = Path(real)
            rows = [{"id":"hinton", "label":"hinton", "file":"hinton", "default":True, "sample_data":True}]
            (real_dir / "hosts.json").write_text(json.dumps(rows) + "\n", encoding="utf-8")
            (real_dir / "hinton.sample.json").write_text(json.dumps(sample_snapshot("hinton")) + "\n", encoding="utf-8")
            link = Path(parent) / "samples-link"
            os.symlink(real_dir, link)
            with self.assertRaises(ValueError):
                serve._DevSampleService(str(link))

    def test_import_ignores_unittest_argv_port_but_script_positional_port_still_works(self):
        code = "import sys; sys.argv=['unittest','viewer.test_serve']; import viewer.serve; print(viewer.serve.PORT)"
        result = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "8088")

class ApiServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="storage-viz-api-test."); self.addCleanup(self.tmp.cleanup)
        self.sample_dir = Path(self.tmp.name) / "samples"; self.sample_dir.mkdir()
        self.write_manifest(["atlas", "hinton"])
        (self.sample_dir / "hinton.sample.json").write_text(json.dumps(sample_snapshot("hinton")) + "\n", encoding="utf-8")
        (self.sample_dir / "atlas.sample.json").write_text(json.dumps(sample_snapshot("atlas")) + "\n", encoding="utf-8")
        self.proc = None; self.addCleanup(self._stop)

    def start_server(self, extra_env=None, expect_exit=False):
        self.port = free_port()
        env = os.environ.copy(); env.update(STORAGE_VIZ_BIND="127.0.0.1", STORAGE_VIZ_PORT=str(self.port), STORAGE_VIZ_DEV_SAMPLE_DIR=str(self.sample_dir))
        if extra_env: env.update(extra_env)
        if env.get("STORAGE_VIZ_DEV_SAMPLE_DIR") == "": env.pop("STORAGE_VIZ_DEV_SAMPLE_DIR", None)
        self.proc = subprocess.Popen([sys.executable, str(SERVE)], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if expect_exit:
            deadline = time.time() + 3
            while time.time() < deadline and self.proc.poll() is None: time.sleep(0.05)
            self.assertIsNotNone(self.proc.poll())
            return
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                self.request("GET", "/api/session")
                return
            except Exception:
                if self.proc.poll() is not None:
                    out, err = self.proc.communicate(timeout=1)
                    self.fail(f"serve.py exited early\nstdout={out}\nstderr={err}")
                time.sleep(0.05)
        self.fail("serve.py did not become ready")

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
        self.start_server({"STORAGE_VIZ_DEV_SAMPLE_DIR":"", "STORAGE_VIZ_INVENTORY":str(inv), "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state")})
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
            STORAGE_VIZ_DEV_SAMPLE_DIR="",
            STORAGE_VIZ_INVENTORY=str(inv),
            STORAGE_VIZ_STATE_DIR=str(Path(self.tmp.name) / "state"),
            STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN="1",
            STORAGE_VIZ_ALLOWED_ORIGINS=f"{origin},{localhost_origin}",
            STORAGE_VIZ_OPERATOR_ALLOWLIST="direct-viewer",
        )
        env.pop("STORAGE_VIZ_DEV_SAMPLE_DIR", None)
        self.proc = subprocess.Popen([sys.executable, str(SERVE)], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                code, headers, sess = self.request("GET", "/api/session")
                break
            except Exception:
                if self.proc.poll() is not None:
                    out, err = self.proc.communicate(timeout=1)
                    self.fail(f"serve.py exited early\nstdout={out}\nstderr={err}")
                time.sleep(0.05)
        else:
            self.fail("serve.py did not become ready")
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
            "STORAGE_VIZ_DEV_SAMPLE_DIR":"",
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
        self.start_server({"STORAGE_VIZ_DEV_SAMPLE_DIR":"", "STORAGE_VIZ_DATA_DIR":str(self.sample_dir), "STORAGE_VIZ_INVENTORY":str(inv), "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"), "STORAGE_VIZ_TRUSTED_PROXY":"1", "STORAGE_VIZ_ALLOWED_ORIGINS":"http://storage.test", "STORAGE_VIZ_OPERATOR_ALLOWLIST":"operator-1"})
        self.assertEqual(self.request("GET", "/api/session")[0], 401)
        self.assertEqual(self.request("GET", "/api/servers")[0], 401)
        self.assertEqual(self.request("GET", "/data/hinton.sample.json")[0], 401)
        code, headers, sess = self.request("GET", "/api/session", headers={"X-Forwarded-User":"viewer-1"})
        self.assertEqual(code, 200); self.assertFalse(sess["can_rescan"])
        viewer_cookie = self.cookie_value(headers)
        self.assertIn("SameSite=Strict", headers.get("Set-Cookie", ""))
        self.assertIn("Secure", headers.get("Set-Cookie", ""))
        code, _, servers = self.request("GET", "/api/servers", headers={"X-Forwarded-User":"viewer-1"})
        self.assertEqual(code, 200); self.assertEqual(servers["data_mode"], "inventory")
        self.assertEqual(self.request("GET", "/data/hinton.sample.json", headers={"X-Forwarded-User":"viewer-1"})[0], 200)
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
        (self.sample_dir / "orphan.sample.json").write_text(json.dumps(sample_snapshot("orphan")) + "\n", encoding="utf-8")
        self.start_server(expect_exit=True)
        self._stop(); self.proc = None
        (self.sample_dir / "orphan.sample.json").unlink()
        (self.sample_dir / ".hidden.sample.json").write_text(json.dumps(sample_snapshot("hidden")) + "\n", encoding="utf-8")
        self.start_server(expect_exit=True)
        self._stop(); self.proc = None
        (self.sample_dir / ".hidden.sample.json").unlink()
        (self.sample_dir / "atlas.sample.json").unlink()
        (self.sample_dir / "outside.sample.json").write_text(json.dumps(sample_snapshot("outside")) + "\n", encoding="utf-8")
        os.symlink(self.sample_dir / "outside.sample.json", self.sample_dir / "atlas.sample.json")
        self.start_server(expect_exit=True)
        self._stop(); self.proc = None
        (self.sample_dir / "atlas.sample.json").unlink()
        self.write_manifest(["escape"])
        (self.sample_dir / "hosts.json").write_text(json.dumps([{"id":"escape", "label":"escape", "file":"../escape", "default":True, "sample_data":True}]) + "\n", encoding="utf-8")
        self.start_server(expect_exit=True)


    def test_direct_script_positional_port_is_preserved(self):
        self.port = free_port()
        env = os.environ.copy()
        env.update(STORAGE_VIZ_BIND="127.0.0.1", STORAGE_VIZ_DEV_SAMPLE_DIR=str(self.sample_dir))
        env.pop("STORAGE_VIZ_PORT", None)
        self.proc = subprocess.Popen([sys.executable, str(SERVE), str(self.port)], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                code, _, _ = self.request("GET", "/api/session")
                self.assertEqual(code, 200)
                return
            except Exception:
                if self.proc.poll() is not None:
                    out, err = self.proc.communicate(timeout=1)
                    self.fail(f"serve.py exited early\nstdout={out}\nstderr={err}")
                time.sleep(0.05)
        self.fail("serve.py did not become ready on positional port")

    def test_expired_session_cookie_is_rejected_on_post(self):
        inv = self.write_inventory()
        self.start_server({"STORAGE_VIZ_DEV_SAMPLE_DIR":"", "STORAGE_VIZ_INVENTORY":str(inv), "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"), "STORAGE_VIZ_TRUSTED_PROXY":"1", "STORAGE_VIZ_ALLOWED_ORIGINS":"http://storage.test", "STORAGE_VIZ_OPERATOR_ALLOWLIST":"operator-1", "STORAGE_VIZ_SESSION_TTL_SECONDS":"1"})
        code, headers, sess = self.request("GET", "/api/session", headers={"X-Forwarded-User":"operator-1"})
        self.assertEqual(code, 200)
        cookie = self.cookie_value(headers)
        time.sleep(1.2)
        self.assertEqual(self.request("POST", "/api/servers/unknown/rescan", {}, headers={"Cookie":cookie, "X-Forwarded-User":"operator-1", "Origin":"http://storage.test", "X-CSRF-Token":sess["csrf_token"]})[0], 403)

    def test_operator_mode_without_allowed_origin_fails_startup(self):
        inv = self.write_inventory()
        self.start_server({"STORAGE_VIZ_DEV_SAMPLE_DIR":"", "STORAGE_VIZ_INVENTORY":str(inv), "STORAGE_VIZ_STATE_DIR":str(Path(self.tmp.name) / "state"), "STORAGE_VIZ_TRUSTED_PROXY":"1", "STORAGE_VIZ_OPERATOR_ALLOWLIST":"operator-1"}, expect_exit=True)

    def test_dev_sample_rejected_when_proxy_mode_not_loopback_or_combined_with_prod(self):
        self.start_server({"STORAGE_VIZ_TRUSTED_PROXY":"1", "STORAGE_VIZ_BIND":"0.0.0.0"}, expect_exit=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
