#!/usr/bin/env python3
"""Small same-origin proxy for the Storage Monitor dashboard.

The dashboard backend intentionally accepts manual rescans only through its
loopback-origin CSRF contract. This proxy exposes a read-only UI on the
configured LAN address, preserves the browser Host, and forwards only GET/HEAD.
"""

from __future__ import annotations

import http.client
import http.server
import os
import socketserver
from typing import Mapping


BIND = os.environ.get("STORAGE_VIZ_PROXY_BIND", "192.168.0.3")
PORT = int(os.environ.get("STORAGE_VIZ_PROXY_PORT", "8088"))
UPSTREAM_HOST = os.environ.get("STORAGE_VIZ_PROXY_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("STORAGE_VIZ_PROXY_UPSTREAM_PORT", "8088"))
UPSTREAM_AUTHORITY = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
RESPONSE_CHUNK_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = int(os.environ.get("STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES", str(512 * 1024 * 1024)))
if not 1 <= MAX_RESPONSE_BYTES <= 512 * 1024 * 1024:
    raise SystemExit("STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES must be between 1 and 536870912")

FORWARDED_RESPONSE_HEADERS = {
    "cache-control",
    "content-encoding",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
    "retry-after",
    "set-cookie",
}


def _header(source: Mapping[str, str], name: str, default: str = "") -> str:
    value = source.get(name)
    if value is not None:
        return str(value)
    lower_name = name.lower()
    for key, candidate in source.items():
        if str(key).lower() == lower_name:
            return str(candidate)
    return default


def build_upstream_headers(source: Mapping[str, str], *, method: str) -> dict[str, str]:
    headers = {
        "Host": _header(source, "Host", UPSTREAM_AUTHORITY),
        "Accept": _header(source, "Accept", "*/*"),
        "Accept-Encoding": _header(source, "Accept-Encoding", "identity"),
        "User-Agent": _header(source, "User-Agent", "storage-viz-direct-proxy"),
        "X-Forwarded-Proto": "http",
        "Connection": "close",
    }
    cookie = _header(source, "Cookie")
    if cookie:
        headers["Cookie"] = cookie
    for name in ("If-Modified-Since", "If-None-Match"):
        value = _header(source, name)
        if value:
            headers[name] = value
    return headers


class ResponseTooLarge(Exception):
    pass


def copy_response_body(response, sink, *, chunk_bytes: int = RESPONSE_CHUNK_BYTES, max_bytes: int = MAX_RESPONSE_BYTES) -> int:
    copied = 0
    while True:
        chunk = response.read(chunk_bytes)
        if not chunk:
            return copied
        copied += len(chunk)
        if copied > max_bytes:
            raise ResponseTooLarge(f"upstream response exceeds {max_bytes} bytes")
        sink.write(chunk)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self, *, method: str, head_only: bool = False) -> None:
        connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=30)
        headers = build_upstream_headers(self.headers, method=method)
        headers["X-Forwarded-For"] = self.client_address[0]
        response_started = False
        try:
            connection.request(method, self.path, headers=headers)
            response = connection.getresponse()
            response_headers = response.getheaders()
            content_length = next((value for name, value in response_headers if name.lower() == "content-length"), None)
            if not head_only and content_length is not None:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise ResponseTooLarge(f"upstream response exceeds {MAX_RESPONSE_BYTES} bytes")
                except ValueError as exc:
                    raise RuntimeError("upstream returned an invalid content length") from exc
            self.send_response(response.status)
            response_started = True
            for name, value in response_headers:
                lower_name = name.lower()
                if lower_name in FORWARDED_RESPONSE_HEADERS:
                    self.send_header(name, value)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if not head_only:
                copy_response_body(response, self.wfile)
        except Exception as exc:
            if response_started:
                self.close_connection = True
                return
            payload = ("storage-viz upstream unavailable: " + str(exc)).encode("utf-8", "replace")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if not head_only:
                self.wfile.write(payload)
        finally:
            connection.close()

    def do_GET(self) -> None:
        self._proxy(method="GET")

    def do_HEAD(self) -> None:
        self._proxy(method="HEAD", head_only=True)

    def _method_not_allowed(self) -> None:
        payload = b"write endpoint not allowed"
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(payload)

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server((BIND, PORT), Handler) as server:
        print(f"storage-viz proxy on {BIND}:{PORT} -> {UPSTREAM_AUTHORITY}", flush=True)
        server.serve_forever()
