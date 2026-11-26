"""Identifier syntax shared by the collector and HTTP server (design §2.1)."""
import re

SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
VOLUME_ID_RE = re.compile(r"^[vx]-[0-9a-f]{16}$")
"""``v-`` for a UUID or alias identity, ``x-`` for a boot-local ambiguous one."""
BINDING_RE = re.compile(r"^b-[0-9a-f]{16}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
