"""Identifier syntax shared by the collector and HTTP server (design §2.1).

Every pattern is anchored with \\A and \\Z, so `match` and `fullmatch` agree
and a trailing newline never passes.
"""
import re

SERVER_ID_RE = re.compile(r"\A(?!\.{1,2}\Z)[A-Za-z0-9_.-]{1,128}\Z")
VOLUME_ID_RE = re.compile(r"\A[vx]-[0-9a-f]{16}\Z")
"""``v-`` for a UUID or alias identity, ``x-`` for a boot-local ambiguous one."""
BINDING_RE = re.compile(r"\Ab-[0-9a-f]{16}\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
REQUEST_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
RUN_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
RELEASE_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
