import re

SERVER_ID_RE = re.compile(r"\A(?!\.{1,2}\Z)[A-Za-z0-9_.-]{1,128}\Z")
VOLUME_ID_RE = re.compile(r"[vx]-[0-9a-f]{16}")
BINDING_RE = re.compile(r"b-[0-9a-f]{16}")
REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}")
