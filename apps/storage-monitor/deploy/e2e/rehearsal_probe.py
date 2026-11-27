#!/usr/bin/env python3
"""Drive the rehearsal's central dashboard through its proxy, as an operator's browser would.

Runs inside the central container. It connects to the proxy listener named in
/etc/storage-viz/proxy.env with the public Host and Origin, takes a session, and prints
one JSON line per command for run-dashboard-upgrade-rehearsal.sh to assert on.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import sys
import time

APP = Path("/opt/storage-viz-dashboard")


def env(path: str) -> dict[str, str]:
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class Client:
    def __init__(self) -> None:
        proxy = env("/etc/storage-viz/proxy.env")
        self.origin = proxy["STORAGE_VIZ_PROXY_PUBLIC_ORIGIN"]
        self.host = self.origin.split("://", 1)[1]
        self.addr = (proxy["STORAGE_VIZ_PROXY_BIND"], int(proxy["STORAGE_VIZ_PROXY_PORT"]))
        self.cookie = ""
        self.csrf = ""

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        connection = http.client.HTTPConnection(*self.addr, timeout=30)
        sent = {"Host": self.host, **({"Cookie": self.cookie} if self.cookie else {}), **(headers or {})}
        if body is not None:
            sent.update({"Content-Type": "application/json", "Origin": self.origin, "X-CSRF-Token": self.csrf})
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def json(self, method: str, path: str, body: bytes | None = None):
        status, headers, raw = self.request(method, path, body)
        try:
            return status, headers, json.loads(raw)
        except ValueError:
            return status, headers, raw.decode("utf-8", "replace")[:200]

    def login(self, session_path: str) -> dict:
        status, headers, session = self.json("GET", session_path)
        if status != 200:
            raise SystemExit(f"session {session_path} -> {status}: {session}")
        self.cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        self.csrf = session.get("csrf_token", "")
        return session


def summarize_fleet(fleet: dict) -> list[dict]:
    rows = []
    for server in fleet.get("servers", []):
        rows.append({
            "server_id": server.get("server_id"), "status": server.get("status"),
            "link": (server.get("link") or {}).get("state"), "freshness": server.get("freshness"),
            "volumes": [{
                "volume_id": v.get("volume_id"), "mountpoint": v.get("mountpoint"),
                "total": (v.get("capacity") or {}).get("total"), "used": (v.get("capacity") or {}).get("used"),
                "capture": (v.get("capture") or {}).get("sha256"), "data_state": v.get("data_state"),
            } for v in server.get("volumes") or []],
        })
    return rows


def cmd_fleet(client: Client, _args) -> dict:
    client.login("/api/v2/session")
    status, _, fleet = client.json("GET", "/api/v2/fleet")
    return {"status": status, "rollout": fleet.get("rollout") if isinstance(fleet, dict) else None,
            "servers": summarize_fleet(fleet) if isinstance(fleet, dict) else fleet}


def cmd_servers_v1(client: Client, _args) -> dict:
    client.login("/api/session")
    status, _, payload = client.json("GET", "/api/servers")
    servers = payload.get("servers", []) if isinstance(payload, dict) else []
    return {"status": status, "data_mode": payload.get("data_mode") if isinstance(payload, dict) else None,
            "servers": [{"id": s.get("id"), "snapshot_availability": s.get("snapshot_availability"), "freshness": s.get("freshness")}
                        for s in servers]}


def cmd_viewer(client: Client, _args) -> dict:
    """The page and every viewer file the release ships come back byte for byte."""
    manifest = json.loads((APP / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
    files = sorted(p for p in manifest["files"] if p.startswith("viewer/") and p.rsplit(".", 1)[-1] in {"html", "js", "css"}
                   and "/test/" not in p and "/browser/" not in p and not p.endswith("serve.py"))
    index_status, headers, index = client.request("GET", "/")
    bad = [] if index_status == 200 and index == (APP / "viewer/index.html").read_bytes() else ["/"]
    for rel in files:
        path = "/" + rel[len("viewer/"):]
        status, _, raw = client.request("GET", path)
        if status != 200 or hashlib.sha256(raw).hexdigest() != manifest["files"][rel]:
            bad.append(f"{path} -> {status}")
    return {"index_status": index_status, "content_type": headers.get("Content-Type"), "assets": len(files), "bad": bad}


def cmd_rescan(client: Client, args) -> dict:
    """POST a rescan, then follow the activity route until that request's job ends."""
    client.login("/api/v2/session")
    status, _, body = client.json("POST", f"/api/v2/servers/{args.server}/rescan", b"{}")
    request_id = ((body.get("job") or {}) if isinstance(body, dict) else {}).get("request_id")
    result = {"post_status": status, "post_body": body, "polls": [], "last_job": None}
    deadline = time.time() + args.timeout
    while request_id and time.time() < deadline:
        _, _, activity = client.json("GET", f"/api/v2/servers/{args.server}/activity")
        job = activity.get("job") if isinstance(activity, dict) else None
        state = (job or {}).get("state")
        if not result["polls"] or result["polls"][-1] != state:
            result["polls"].append(state)
        last = activity.get("last_job") if isinstance(activity, dict) else None
        if job is None and last and last.get("request_id") == request_id:
            result["last_job"] = last
            break
        time.sleep(1)
    return result


def cmd_rescan_v1(client: Client, args) -> dict:
    client.login("/api/session")
    status, _, body = client.json("POST", f"/api/servers/{args.server}/rescan", b"{}")
    return {"post_status": status, "post_body": body}


COMMANDS = {"fleet": cmd_fleet, "servers-v1": cmd_servers_v1, "viewer": cmd_viewer,
            "rescan": cmd_rescan, "rescan-v1": cmd_rescan_v1}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--server")
    parser.add_argument("--timeout", type=int, default=240)
    parsed = parser.parse_args()
    print(json.dumps(COMMANDS[parsed.command](Client(), parsed), sort_keys=True))
    sys.exit(0)
