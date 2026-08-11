#!/usr/bin/env python3
"""Regression tests for the public Storage Monitor proxy."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("direct_proxy.py")
spec = importlib.util.spec_from_file_location("storage_direct_proxy", MODULE_PATH)
proxy = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(proxy)


class HeaderForwardingTests(unittest.TestCase):
    def test_session_request_uses_loopback_host_and_forwards_cookie(self) -> None:
        headers = proxy.build_upstream_headers(
            {
                "Host": "166.104.167.11:505",
                "Accept": "application/json",
                "Cookie": "storage_viz_session=abc",
            },
            method="GET",
        )
        self.assertEqual(headers["Host"], "127.0.0.1:8088")
        self.assertEqual(headers["Cookie"], "storage_viz_session=abc")
        self.assertNotIn("Origin", headers)

    def test_rescan_post_rewrites_origin_and_forwards_csrf_body_headers(self) -> None:
        headers = proxy.build_upstream_headers(
            {
                "Host": "166.104.167.11:505",
                "Origin": "http://166.104.167.11:505",
                "Cookie": "storage_viz_session=abc",
                "X-CSRF-Token": "token-1",
                "Content-Type": "application/json",
                "Content-Length": "2",
            },
            method="POST",
        )
        self.assertEqual(headers["Host"], "127.0.0.1:8088")
        self.assertEqual(headers["Origin"], "http://127.0.0.1:8088")
        self.assertEqual(headers["Cookie"], "storage_viz_session=abc")
        self.assertEqual(headers["X-CSRF-Token"], "token-1")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Content-Length"], "2")

    def test_only_bounded_rescan_posts_are_allowed(self) -> None:
        self.assertTrue(proxy.is_allowed_post("/api/servers/hinton/rescan", 2))
        self.assertFalse(proxy.is_allowed_post("/api/servers", 2))
        self.assertFalse(proxy.is_allowed_post("/api/servers/hinton/rescan", 0))
        self.assertFalse(proxy.is_allowed_post("/api/servers/hinton/rescan", 4097))
        self.assertFalse(proxy.is_allowed_post("/api/servers/../rescan", 2))

    def test_session_cookie_and_retry_headers_are_returned_to_browser(self) -> None:
        self.assertIn("set-cookie", proxy.FORWARDED_RESPONSE_HEADERS)
        self.assertIn("retry-after", proxy.FORWARDED_RESPONSE_HEADERS)


if __name__ == "__main__":
    unittest.main()
