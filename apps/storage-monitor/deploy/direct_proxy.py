#!/usr/bin/env python3
"""Small same-origin proxy for the Storage Monitor dashboard.

The dashboard backend intentionally accepts manual rescans only through its
loopback-origin CSRF contract. This proxy exposes that UI on the configured LAN
address while rewriting the upstream Host/Origin to the loopback endpoint. It
forwards only GET/HEAD plus the one bounded rescan POST route.
"""

from __future__ import annotations

import http.client
import http.server
import os
import re
import socketserver
from typing import Mapping
from urllib.parse import urlsplit


BIND = os.environ.get("STORAGE_VIZ_PROXY_BIND", "192.168.0.3")
PORT = int(os.environ.get("STORAGE_VIZ_PROXY_PORT", "8088"))
UPSTREAM_HOST = os.environ.get("STORAGE_VIZ_PROXY_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("STORAGE_VIZ_PROXY_UPSTREAM_PORT", "8088"))
UPSTREAM_AUTHORITY = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
UPSTREAM_ORIGIN = f"http://{UPSTREAM_AUTHORITY}"
MAX_POST_BYTES = 4096
RESCAN_PATH = re.compile(r"^/api/servers/[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?/rescan$")

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
        "Host": UPSTREAM_AUTHORITY,
        "Accept": _header(source, "Accept", "*/*"),
        "Accept-Encoding": _header(source, "Accept-Encoding", "identity"),
        "User-Agent": _header(source, "User-Agent", "storage-viz-direct-proxy"),
        "X-Forwarded-Proto": "http",
        "Connection": "close",
    }
    forwarded_for = _header(source, "X-Forwarded-For")
    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for
    cookie = _header(source, "Cookie")
    if cookie:
        headers["Cookie"] = cookie
    if method.upper() == "POST":
        headers["Origin"] = UPSTREAM_ORIGIN
        for name in ("X-CSRF-Token", "Content-Type", "Content-Length"):
            value = _header(source, name)
            if value:
                headers[name] = value
    return headers


def is_allowed_post(path: str, content_length: int) -> bool:
    route = urlsplit(path).path
    return 0 < content_length <= MAX_POST_BYTES and bool(RESCAN_PATH.fullmatch(route))


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self, *, method: str, body: bytes | None = None, head_only: bool = False) -> None:
        connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=30)
        headers = build_upstream_headers(self.headers, method=method)
        forwarded_for = self.headers.get("X-Forwarded-For", "").strip()
        client_ip = self.client_address[0]
        headers["X-Forwarded-For"] = f"{forwarded_for}, {client_ip}" if forwarded_for else client_ip
        try:
            connection.request(method, self.path, body=body, headers=headers)
            response = connection.getresponse()
            response_headers = response.getheaders()
            payload = b"" if head_only else response.read()
            self.send_response(response.status)
            has_content_length = False
            for name, value in response_headers:
                lower_name = name.lower()
                if lower_name in FORWARDED_RESPONSE_HEADERS:
                    self.send_header(name, value)
                    has_content_length = has_content_length or lower_name == "content-length"
            if not head_only and not has_content_length:
                self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Connection", "close")
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except Exception as exc:
            payload = ("storage-viz upstream unavailable: " + str(exc)).encode("utf-8", "replace")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)
        finally:
            connection.close()

    def do_GET(self) -> None:
        self._proxy(method="GET")

    def do_HEAD(self) -> None:
        self._proxy(method="HEAD", head_only=True)

    def do_POST(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            content_length = -1
        if not is_allowed_post(self.path, content_length):
            return self._method_not_allowed()
        body = self.rfile.read(content_length)
        self._proxy(method="POST", body=body)

    def _method_not_allowed(self) -> None:
        payload = b"write endpoint not allowed"
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, POST")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

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
