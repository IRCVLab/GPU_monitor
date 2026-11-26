import re

SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VOLUME_ID_RE = re.compile(r"[vx]-[0-9a-f]{16}")
BINDING_RE = re.compile(r"b-[0-9a-f]{16}")
REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}")
