"""Identifier syntax shared by the collector and HTTP server."""
import re

SERVER_ID_RE = re.compile(r"\A(?!\.{1,2}\Z)[A-Za-z0-9_.-]{1,128}\Z")
