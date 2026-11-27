#!/usr/bin/env python3
"""A local stand-in for the GitHub API the release pullers read, for the upgrade rehearsal.

It serves HTTPS as api.github.com inside the rehearsal's central container (the
container trusts a throwaway CA and maps the name to 127.0.0.1), so the installed
pullers run unmodified with their hard-coded API base. The shapes follow
FakeGitHub in tests/test_storage_release_puller.py. ``main`` is whatever the bare
repository's refs/heads/main names, so a merge is a ref update, and every main SHA
has a successful ci.yml push run and ci/required check. No agent-bundle run exists:
the first deploy releases the dashboard without the agent channel.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import ssl
import subprocess
import time
from urllib.parse import urlsplit

def main_sha(repo: Path) -> str:
    return subprocess.run(["git", "--git-dir", str(repo), "rev-parse", "refs/heads/main"],
                          check=True, capture_output=True, text=True).stdout.strip()


def answer(path: str, repository: str, repo: Path) -> object | None:
    parts = urlsplit(path)
    route = parts.path
    sha = main_sha(repo)
    prefix = f"/repos/{repository}"
    if route == f"{prefix}/git/ref/heads/main":
        return {"ref": "refs/heads/main", "object": {"sha": sha, "type": "commit"}}
    if route == f"{prefix}/actions/workflows/ci.yml/runs":
        return {"total_count": 1, "workflow_runs": [{
            "id": 123, "name": "ci", "event": "push", "head_branch": "main", "head_sha": sha,
            "status": "completed", "conclusion": "success", "path": ".github/workflows/ci.yml",
            "head_repository": {"full_name": repository},
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200)),
        }]}
    match = re.fullmatch(rf"{re.escape(prefix)}/commits/([0-9a-f]{{40}})/check-runs", route)
    if match:
        checks = [] if match.group(1) != sha else [{
            "id": 99, "name": "ci/required", "head_sha": sha, "status": "completed",
            "conclusion": "success", "completed_at": "2026-11-27T00:00:00Z",
        }]
        return {"total_count": len(checks), "check_runs": checks}
    if route == f"{prefix}/actions/workflows/agent-bundle.yml/runs":
        return {"total_count": 0, "workflow_runs": []}
    return None


def serve(args: argparse.Namespace) -> None:
    log = Path(args.log)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            payload = answer(self.path, args.repository, Path(args.repo))
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.time():.3f} GET {self.path} {'200' if payload is not None else '404'}\n")
            body = json.dumps(payload if payload is not None else {"message": "Not Found"}).encode()
            self.send_response(200 if payload is not None else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="bare repository whose main the API reports")
    parser.add_argument("--repository", default="IRCVLab/GPU_monitor")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--log", required=True)
    serve(parser.parse_args())
