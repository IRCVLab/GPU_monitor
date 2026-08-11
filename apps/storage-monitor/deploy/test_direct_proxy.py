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

    def test_lan_request_cannot_spoof_the_loopback_upstream_host(self) -> None:
        headers = proxy.build_upstream_headers(
            {"Host": proxy.UPSTREAM_AUTHORITY},
            method="GET",
        )
        self.assertEqual(headers["Host"], "storage-viz-proxy.invalid")

    def test_lan_proxy_has_no_rescan_post_handler(self) -> None:
        self.assertIs(proxy.Handler.do_POST, proxy.Handler._method_not_allowed)

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
