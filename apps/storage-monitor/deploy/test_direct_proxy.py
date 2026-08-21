#!/usr/bin/env python3
"""Regression tests for the public Storage Monitor proxy."""

from __future__ import annotations

import importlib.util
import http.client
import http.server
from pathlib import Path
import threading
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("direct_proxy.py")
spec = importlib.util.spec_from_file_location("storage_direct_proxy", MODULE_PATH)
proxy = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(proxy)


class HeaderForwardingTests(unittest.TestCase):
    def test_lan_request_preserves_original_host_and_forwards_cookie(self) -> None:
        headers = proxy.build_upstream_headers(
            {
                "Host": "166.104.167.11:505",
                "Accept": "application/json",
                "Cookie": "storage_viz_session=abc",
            },
            method="GET",
        )
        self.assertEqual(headers["Host"], "166.104.167.11:505")
        self.assertEqual(headers["Cookie"], "storage_viz_session=abc")
        self.assertNotIn("Origin", headers)

    def test_operator_proxy_injects_fixed_identity_and_forwards_rescan_proof_headers(self) -> None:
        with (
            mock.patch.object(proxy, "OPERATOR_ID", "lan-operator"),
            mock.patch.object(proxy, "PUBLIC_ORIGIN", "http://166.104.167.11:505"),
        ):
            headers = proxy.build_upstream_headers(
                {
                    "Host": "166.104.167.11:505",
                    "Origin": "http://166.104.167.11:505",
                    "Cookie": "storage_viz_session=abc",
                    "X-Forwarded-User": "spoofed-user",
                    "X-CSRF-Token": "csrf-1",
                    "Content-Type": "application/json",
                    "Content-Length": "2",
                },
                method="POST",
            )
        self.assertEqual(headers["Host"], "166.104.167.11:505")
        self.assertEqual(headers["X-Forwarded-User"], "lan-operator")
        self.assertEqual(headers["Origin"], "http://166.104.167.11:505")
        self.assertEqual(headers["Cookie"], "storage_viz_session=abc")
        self.assertEqual(headers["X-CSRF-Token"], "csrf-1")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Content-Length"], "2")

    def test_only_exact_same_origin_empty_json_rescan_posts_are_allowed(self) -> None:
        origin = "http://166.104.167.11:505"
        host = "166.104.167.11:505"
        self.assertTrue(proxy.is_allowed_rescan_request(
            "/api/servers/hinton/rescan", content_length=2, origin=origin, host=host, public_origin=origin
        ))
        self.assertTrue(proxy.is_empty_rescan_body(b"{}"))
        for path, length, request_origin, request_host in (
            ("/api/servers", 2, origin, host),
            ("/api/servers/hinton/rescan?force=1", 2, origin, host),
            ("/api/servers/../rescan", 2, origin, host),
            ("http://evil.test/api/servers/hinton/rescan", 2, origin, host),
            ("//evil.test/api/servers/hinton/rescan", 2, origin, host),
            ("/api/servers/hinton/rescan", 0, origin, host),
            ("/api/servers/hinton/rescan", 4097, origin, host),
            ("/api/servers/hinton/rescan", 2, "http://evil.test", host),
            ("/api/servers/hinton/rescan", 2, origin, "evil.test"),
        ):
            with self.subTest(path=path, length=length, origin=request_origin, host=request_host):
                self.assertFalse(proxy.is_allowed_rescan_request(
                    path,
                    content_length=length,
                    origin=request_origin,
                    host=request_host,
                    public_origin=origin,
                ))
        for body in (b"", b"[]", b'{"path":"/"}', b"{}{}", b"not-json"):
            with self.subTest(body=body):
                self.assertFalse(proxy.is_empty_rescan_body(body))

    def test_rescan_path_matches_the_backend_server_id_contract(self) -> None:
        origin = "http://166.104.167.11:505"
        host = "166.104.167.11:505"
        for server_id in ("_lab", "gpu_", ".gpu", "gpu.", "a" * 128):
            with self.subTest(server_id=server_id):
                self.assertTrue(proxy.is_allowed_rescan_request(
                    f"/api/servers/{server_id}/rescan",
                    content_length=2,
                    origin=origin,
                    host=host,
                    public_origin=origin,
                ))
        for server_id in (".", "..", "a" * 129, "gpu%2Fnode"):
            with self.subTest(server_id=server_id):
                self.assertFalse(proxy.is_allowed_rescan_request(
                    f"/api/servers/{server_id}/rescan",
                    content_length=2,
                    origin=origin,
                    host=host,
                    public_origin=origin,
                ))

    def test_post_handler_forwards_only_the_exact_rescan_route(self) -> None:
        captured = []

        class UpstreamHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                captured.append((self.path, dict(self.headers), self.rfile.read(length)))
                payload = b'{"status":"started"}'
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format, *_args) -> None:
                pass

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        public = http.server.ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
        threads = [
            threading.Thread(target=upstream.serve_forever, daemon=True),
            threading.Thread(target=public.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        origin = f"http://127.0.0.1:{public.server_port}"
        try:
            with (
                mock.patch.object(proxy, "UPSTREAM_HOST", "127.0.0.1"),
                mock.patch.object(proxy, "UPSTREAM_PORT", upstream.server_port),
                mock.patch.object(proxy, "OPERATOR_ID", "lan-operator"),
                mock.patch.object(proxy, "PUBLIC_ORIGIN", origin),
                mock.patch.object(proxy, "RESCAN_POST_ENABLED", True),
            ):
                conn = http.client.HTTPConnection("127.0.0.1", public.server_port, timeout=3)
                conn.request(
                    "POST",
                    "/api/servers/_lab/rescan",
                    body=b"{}",
                    headers={
                        "Host": f"127.0.0.1:{public.server_port}",
                        "Origin": origin,
                        "Cookie": "storage_viz_session=abc",
                        "X-CSRF-Token": "csrf-1",
                        "Content-Type": "application/json",
                    },
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 202)
                self.assertEqual(response.read(), b'{"status":"started"}')
                conn.close()

                conn = http.client.HTTPConnection("127.0.0.1", public.server_port, timeout=3)
                conn.request(
                    "POST",
                    "/api/servers/_lab/rescan?force=1",
                    body=b"{}",
                    headers={"Host": f"127.0.0.1:{public.server_port}", "Origin": origin},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 405)
                response.read()
                conn.close()
        finally:
            public.shutdown()
            upstream.shutdown()
            public.server_close()
            upstream.server_close()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual(len(captured), 1)
        path, headers, body = captured[0]
        self.assertEqual(path, "/api/servers/_lab/rescan")
        self.assertEqual(headers["Origin"], origin)
        self.assertEqual(headers["X-Forwarded-User"], "lan-operator")
        self.assertEqual(headers["X-CSRF-Token"], "csrf-1")
        self.assertEqual(headers["Content-Length"], "2")
        self.assertEqual(body, b"{}")

    def test_post_handler_stays_disabled_without_operator_mode(self) -> None:
        public = http.server.ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
        thread = threading.Thread(target=public.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.object(proxy, "RESCAN_POST_ENABLED", False):
                conn = http.client.HTTPConnection("127.0.0.1", public.server_port, timeout=3)
                conn.request("POST", "/api/servers/hinton/rescan", body=b"{}")
                response = conn.getresponse()
                self.assertEqual(response.status, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                response.read()
                conn.close()
        finally:
            public.shutdown()
            public.server_close()
            thread.join(timeout=2)

    def test_lan_request_cannot_spoof_the_loopback_upstream_host(self) -> None:
        headers = proxy.build_upstream_headers(
            {"Host": proxy.UPSTREAM_AUTHORITY},
            method="GET",
        )
        self.assertEqual(headers["Host"], "storage-viz-proxy.invalid")

    def test_lan_proxy_keeps_operator_posts_disabled_without_explicit_configuration(self) -> None:
        self.assertFalse(proxy.rescan_post_enabled(operator_id="", public_origin=""))
        self.assertFalse(proxy.rescan_post_enabled(operator_id="lan-operator", public_origin=""))
        self.assertFalse(proxy.rescan_post_enabled(operator_id="", public_origin="http://166.104.167.11:505"))
        self.assertTrue(proxy.rescan_post_enabled(operator_id="lan-operator", public_origin="http://166.104.167.11:505"))

    def test_operator_proxy_rejects_partial_or_non_exact_configuration(self) -> None:
        for operator, origin in (
            ("lan-operator", ""),
            ("", "http://storage.test:505"),
            ("bad\noperator", "http://storage.test:505"),
            ("lan-operator", "http://user@storage.test:505"),
            ("lan-operator", "http://storage.test:505/path"),
            ("lan-operator", "http://storage.test:notaport"),
            ("lan-operator", "http://storage.test:"),
        ):
            with self.subTest(operator=operator, origin=origin):
                with self.assertRaises(SystemExit):
                    proxy._validate_operator_config(operator, origin)

    def test_session_cookie_and_retry_headers_are_returned_to_browser(self) -> None:
        self.assertIn("set-cookie", proxy.FORWARDED_RESPONSE_HEADERS)
        self.assertIn("retry-after", proxy.FORWARDED_RESPONSE_HEADERS)

    def test_response_body_is_streamed_in_bounded_chunks(self) -> None:
        class Response:
            def __init__(self) -> None:
                self.remaining = b"abcdefghijk"
                self.read_sizes = []

            def read(self, size: int) -> bytes:
                self.read_sizes.append(size)
                chunk, self.remaining = self.remaining[:size], self.remaining[size:]
                return chunk

        class Sink:
            def __init__(self) -> None:
                self.parts = []

            def write(self, value: bytes) -> None:
                self.parts.append(value)

        response = Response()
        sink = Sink()
        copied = proxy.copy_response_body(response, sink, chunk_bytes=4, max_bytes=32)
        self.assertEqual(copied, 11)
        self.assertEqual(b"".join(sink.parts), b"abcdefghijk")
        self.assertTrue(response.read_sizes)
        self.assertTrue(all(size == 4 for size in response.read_sizes))

    def test_response_body_stream_enforces_size_limit(self) -> None:
        class Response:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, _size: int) -> bytes:
                self.calls += 1
                return b"1234" if self.calls <= 2 else b""

        class Sink:
            def write(self, _value: bytes) -> None:
                pass

        with self.assertRaises(proxy.ResponseTooLarge):
            proxy.copy_response_body(Response(), Sink(), chunk_bytes=4, max_bytes=6)


if __name__ == "__main__":
    unittest.main()
