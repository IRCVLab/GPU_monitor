"""Convert a v1 snapshot to deterministic synthetic fixture JSON on stdout."""
import argparse
import hashlib
import json
from pathlib import Path


def scrub(snapshot):
    """Preserve measurements and relationships, replacing identifying strings."""
    def token(kind, value):
        digest = hashlib.sha256(str(value).encode()).hexdigest()[:16]
        return f"{kind}-{digest}"

    def path(value):
        return "/".join(token("entry", part) if part else "" for part in value.split("/"))

    # Display paths are sufficient for a synthetic fixture. Never retain a
    # second copy of a source path in its base64 representation or diagnostics.
    def visit(value, key=""):
        if isinstance(value, list):
            return [visit(row, key) for row in value]
        if isinstance(value, dict):
            if key == "by_mount":
                return {path(mount): size for mount, size in value.items()}
            out = {}
            for field, item in value.items():
                if field.endswith("_b64"):
                    continue
                if field == "name" and key == "users":
                    out[field] = token("user", item)
                else:
                    out[field] = visit(item, field)
            return out
        if value is None:
            return None
        if key == "uid":
            return int(token("uid", value)[4:14], 16) % 2147483647
        if isinstance(value, str):
            if key in {"name", "raw_name", "path", "scan_root", "mount_root", "mountpoint", "mount_source"}:
                return path(value)
            if key in {"user", "owner", "username"}:
                return token("user", value)
            if key in {"server_id", "hostname"}:
                return token("server", value)
            if key in {"status", "kind", "storage_media", "storage_media_confidence", "fstype", "severity",
                       "error_code", "reason", "major_minor", "scanner_version", "config_digest"}:
                # Preserve only bounded schema tokens; scrub free-form diagnostic text.
                if key in {"error_code", "reason"} and value not in {"EACCES", "EIO", "ENOENT", "EPERM"}:
                    return "OTHER"
                return value
            return token("value", value)
        return value

    if snapshot.get("schema_version") != 1:
        raise ValueError("expected a v1 snapshot")
    return visit(snapshot)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    print(json.dumps(scrub(json.loads(args.snapshot.read_bytes())), indent=2, sort_keys=True))
