"""Identifier syntax shared by the collector and HTTP server."""
import re

SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
