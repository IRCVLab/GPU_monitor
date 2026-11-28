"""Minimal RouterOS API client over plain TCP, for read-only queries.

Wire format: a sentence is a run of words ended by an empty word. A word is a
length prefix of 1 to 5 bytes, then that many bytes.
"""
from __future__ import annotations

import socket
from typing import Iterable

MAX_WORD_BYTES = 1 << 20


class RouterOSError(Exception):
    """The switch refused a request, closed the connection, or sent bytes we cannot parse."""


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    if length < 0x4000:
        return (length | 0x8000).to_bytes(2, "big")
    if length < 0x200000:
        return (length | 0xC00000).to_bytes(3, "big")
    if length < 0x10000000:
        return (length | 0xE0000000).to_bytes(4, "big")
    return b"\xf0" + length.to_bytes(4, "big")


def encode_sentence(words: Iterable[str]) -> bytes:
    out = bytearray()
    for word in words:
        raw = word.encode()
        out += encode_length(len(raw)) + raw
    return bytes(out) + b"\0"


class Connection:
    """One logged-in API session. ``sock`` needs ``sendall``, ``recv`` and ``close``."""

    def __init__(self, sock) -> None:
        self._sock = sock

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def _read(self, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = self._sock.recv(count - len(data))
            if not chunk:
                raise RouterOSError("connection closed by the switch")
            data += chunk
        return bytes(data)

    def _read_length(self) -> int:
        first = self._read(1)[0]
        if first < 0x80:
            return first
        if first < 0xC0:
            return ((first & 0x3F) << 8) | self._read(1)[0]
        if first < 0xE0:
            return int.from_bytes(bytes([first & 0x1F]) + self._read(2), "big")
        if first < 0xF0:
            return int.from_bytes(bytes([first & 0x0F]) + self._read(3), "big")
        if first == 0xF0:
            return int.from_bytes(self._read(4), "big")
        raise RouterOSError("unexpected control byte in a length prefix")

    def _read_sentence(self) -> list[str]:
        words = []
        while True:
            length = self._read_length()
            if length == 0:
                return words
            if length > MAX_WORD_BYTES:
                raise RouterOSError("word longer than the limit")
            words.append(self._read(length).decode("utf-8", "replace"))

    def talk(self, words: list[str], secret: str = "") -> list[dict[str, str]]:
        """Send one command and return its ``!re`` rows as dicts, or raise on ``!trap``/``!fatal``.

        ``secret`` is scrubbed from any error text the switch sends back.
        """
        self._sock.sendall(encode_sentence(words))
        rows: list[dict[str, str]] = []
        trap = None
        while True:
            sentence = self._read_sentence()
            if not sentence:
                continue
            kind, attrs = sentence[0], {}
            for word in sentence[1:]:
                if word.startswith("="):
                    key, _, value = word[1:].partition("=")
                    attrs[key] = value
            if kind == "!re":
                rows.append(attrs)
            elif kind == "!trap" and trap is None:
                trap = attrs.get("message", "request refused")
            elif kind == "!fatal":
                raise RouterOSError(_scrub("switch closed the session: " + " ".join(sentence[1:]), secret))
            elif kind == "!done":
                if trap is not None:
                    raise RouterOSError(_scrub(trap, secret))
                return rows



def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def connect(host: str, port: int, user: str, password: str, timeout: float = 5.0) -> Connection:
    sock = socket.create_connection((host, port), timeout=timeout)
    connection = Connection(sock)
    try:
        connection.talk(["/login", "=name=" + user, "=password=" + password], secret=password)
    except BaseException:
        connection.close()
        raise
    return connection
