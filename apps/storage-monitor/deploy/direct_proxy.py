#!/usr/bin/env python3
"""Small same-origin proxy for the Storage Monitor dashboard.

The default remains read-only. An explicitly configured internal operator mode
adds only the bounded per-server rescan POST route; every other write stays
blocked and the dashboard backend still enforces its session and CSRF contract.
"""

from __future__ import annotations

import http.client
import http.server
import json
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
UNTRUSTED_LOOPBACK_HOST = "storage-viz-proxy.invalid"
OPERATOR_ID = os.environ.get("STORAGE_VIZ_PROXY_OPERATOR", "").strip()
PUBLIC_ORIGIN = os.environ.get("STORAGE_VIZ_PROXY_PUBLIC_ORIGIN", "").strip()
MAX_POST_BYTES = 4096
RESCAN_PATH = re.compile(r"^/api/servers/([A-Za-z0-9_.-]{1,128})/rescan$")
RESPONSE_CHUNK_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = int(os.environ.get("STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES", str(512 * 1024 * 1024)))
if not 1 <= MAX_RESPONSE_BYTES <= 512 * 1024 * 1024:
    raise SystemExit("STORAGE_VIZ_PROXY_MAX_RESPONSE_BYTES must be between 1 and 536870912")


def rescan_post_enabled(*, operator_id: str, public_origin: str) -> bool:
    return bool(operator_id.strip() and public_origin.strip())


def _validate_operator_config(operator_id: str, public_origin: str) -> None:
    if bool(operator_id) != bool(public_origin):
        raise SystemExit("storage rescan proxy requires both operator and public origin")
    if not operator_id:
        return
    if len(operator_id) > 128 or any(char in operator_id for char in "\r\n/\\"):
        raise SystemExit("STORAGE_VIZ_PROXY_OPERATOR is invalid")
    try:
        parsed = urlsplit(public_origin)
        parsed.port
    except ValueError as exc:
        raise SystemExit("STORAGE_VIZ_PROXY_PUBLIC_ORIGIN is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("STORAGE_VIZ_PROXY_PUBLIC_ORIGIN must be an exact HTTP(S) origin")


_validate_operator_config(OPERATOR_ID, PUBLIC_ORIGIN)
RESCAN_POST_ENABLED = rescan_post_enabled(operator_id=OPERATOR_ID, public_origin=PUBLIC_ORIGIN)

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
    forwarded_host = _header(source, "Host", UNTRUSTED_LOOPBACK_HOST)
    if forwarded_host.lower() in {UPSTREAM_AUTHORITY.lower(), f"localhost:{UPSTREAM_PORT}"}:
        forwarded_host = UNTRUSTED_LOOPBACK_HOST
    headers = {
        "Host": forwarded_host,
        "Accept": _header(source, "Accept", "*/*"),
        "Accept-Encoding": _header(source, "Accept-Encoding", "identity"),
        "User-Agent": _header(source, "User-Agent", "storage-viz-direct-proxy"),
        "X-Forwarded-Proto": "http",
        "Connection": "close",
    }
    cookie = _header(source, "Cookie")
    if cookie:
        headers["Cookie"] = cookie
    if OPERATOR_ID:
        headers["X-Forwarded-User"] = OPERATOR_ID
    if method.upper() == "POST" and PUBLIC_ORIGIN:
        headers["Origin"] = PUBLIC_ORIGIN
        for name in ("X-CSRF-Token", "Content-Type", "Content-Length"):
            value = _header(source, name)
            if value:
                headers[name] = value
    for name in ("If-Modified-Since", "If-None-Match"):
        value = _header(source, name)
        if value:
            headers[name] = value
    return headers


def is_allowed_rescan_request(
    path: str,
    *,
    content_length: int,
    origin: str,
    host: str,
    public_origin: str,
) -> bool:
    try:
        request_path = urlsplit(path)
        configured_origin = urlsplit(public_origin)
        configured_origin.port
    except ValueError:
        return False
    match = RESCAN_PATH.fullmatch(request_path.path)
    return (
        0 < content_length <= MAX_POST_BYTES
        and not request_path.scheme
        and not request_path.netloc
        and not request_path.query
        and not request_path.fragment
        and match is not None
        and match.group(1) not in {".", ".."}
        and origin == public_origin
        and host.lower() == configured_origin.netloc.lower()
    )


def is_empty_rescan_body(body: bytes) -> bool:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and not value


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

    def _proxy(self, *, method: str, body: bytes | None = None, head_only: bool = False) -> None:
        connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=30)
        headers = build_upstream_headers(self.headers, method=method)
        headers["X-Forwarded-For"] = self.client_address[0]
        if body is not None:
            headers["Content-Length"] = str(len(body))
        response_started = False
        try:
            connection.request(method, self.path, body=body, headers=headers)
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

    def do_POST(self) -> None:
        if not RESCAN_POST_ENABLED:
            return self._method_not_allowed()
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            content_length = -1
        if not is_allowed_rescan_request(
            self.path,
            content_length=content_length,
            origin=self.headers.get("Origin", ""),
            host=self.headers.get("Host", ""),
            public_origin=PUBLIC_ORIGIN,
        ):
            return self._method_not_allowed()
        body = self.rfile.read(content_length)
        if not is_empty_rescan_body(body):
            payload = b"rescan body must be an empty JSON object"
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(payload)
            return
        self._proxy(method="POST", body=body)

    def _method_not_allowed(self) -> None:
        payload = b"write endpoint not allowed"
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, POST" if RESCAN_POST_ENABLED else "GET, HEAD")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
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
