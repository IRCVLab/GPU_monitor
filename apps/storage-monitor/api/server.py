"""Storage dashboard HTTP server and fleet lifecycle."""
import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import os
from pathlib import Path
import secrets

from api import auth, routes
from api.errors import http_error
from api.fixtures import FixtureFleet
from collector.fleet import Fleet
from collector.inventory import CapacityThresholds, load_inventory
from collector.gate import GateHost, HostRouter
from collector.legacy import LegacyHost
from collector.transport import GateTransport, OpenSshTransport


@dataclass
class Runtime:
    fleet: Fleet | FixtureFleet | None = None
    capacity_thresholds: CapacityThresholds = CapacityThresholds()
    runs_actors: bool = False
    rescan_enabled: bool = False


def configure(fixtures=None):
    if fixtures is not None:
        if os.environ.get("STORAGE_VIZ_ENV") == "production" or auth.PREFLIGHT_BACKEND or auth.DIRECT_LOOPBACK_RESCAN:
            raise SystemExit("fixtures disabled in production, preflight and direct rescan modes")
        return Runtime(fleet=FixtureFleet(fixtures))
    if not auth.INVENTORY_PATH:
        return Runtime()
    inventory = load_inventory(auth.INVENTORY_PATH)
    project = Path(os.environ.get("STORAGE_VIZ_ROOT", routes.VIEWER_DIR.parent))
    state = Path(os.environ.get("STORAGE_VIZ_STATE_DIR", project / ".storage-viz-state")).resolve()
    fleet = Fleet(inventory.servers, state, HostRouter(GateHost(GateTransport()), LegacyHost(OpenSshTransport())),
                  cooldown_seconds=int(os.environ.get("STORAGE_VIZ_RESCAN_COOLDOWN_SECONDS", "900")),
                  max_active_jobs=int(os.environ.get("STORAGE_VIZ_RESCAN_MAX_CONCURRENT", "2")))
    return Runtime(
        fleet=fleet, capacity_thresholds=inventory.capacity_thresholds,
        runs_actors=not auth.PREFLIGHT_BACKEND,
        rescan_enabled=bool(auth.OPERATORS and (auth.TRUSTED_PROXY or auth.DIRECT_LOOPBACK_RESCAN or auth.PREFLIGHT_BACKEND)),
    )


class Handler(auth.Auth, BaseHTTPRequestHandler):
    def handle_request(self):
        request_id = secrets.token_hex(16)
        self.health_probe = False
        try:
            # BaseHTTPRequestHandler rewrites leading slashes. Validate the original target.
            target = self.requestline.split()[1]
            response = routes.dispatch(self, target)
        except Exception as exc:
            status, headers, body = http_error(exc, request_id, health_probe=self.health_probe)
            if status == 500:
                logging.exception("storage-viz request %s failed", request_id)
            response = routes.json_response(body, status, headers)
        self.send_response(response.status)
        self.send_header("X-Request-Id", request_id)
        if "Cache-Control" not in response.headers:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(sum(map(len, response.chunks))))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            for chunk in response.chunks:
                self.wfile.write(chunk)

    def __getattr__(self, name):
        # BaseHTTPRequestHandler dispatches verbs by looking up do_<method>.
        if name.startswith("do_"):
            return self.handle_request
        raise AttributeError(name)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    def __init__(self, address, runtime):
        self.runtime = runtime
        super().__init__(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", type=int, default=os.environ.get("STORAGE_VIZ_PORT", os.environ.get("PORT", "8088")))
    parser.add_argument("--fixtures", type=Path)
    args = parser.parse_args()
    runtime = configure(args.fixtures)
    with Server((auth.BIND, args.port), runtime) as httpd:
        if runtime.runs_actors:
            runtime.fleet.start()
        try:
            print(f"storage-viz serving {routes.VIEWER_DIR} on {auth.BIND}:{httpd.server_port}", flush=True)
            httpd.serve_forever()
        finally:
            if runtime.runs_actors:
                runtime.fleet.stop()


if __name__ == "__main__":
    main()
