"""Storage dashboard HTTP server and fleet lifecycle."""
import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import os
from pathlib import Path
import secrets
import sys
import threading
from typing import Any, Callable

from api import auth, routes
from api.errors import http_error
from api.fixtures import FixtureFleet
from collector.errors import DomainError
from collector.fleet import Fleet
from collector.inventory import CapacityThresholds, load_inventory
from collector.legacy import LegacyHost
from collector.live_capacity import POLL_SECONDS, LiveCapacity
from collector.transport import OpenSshTransport


@dataclass
class Runtime:
    fleet: Fleet | FixtureFleet | None = None
    data_mode: str = "sample"
    capacity_thresholds: CapacityThresholds = CapacityThresholds()
    live_capacity: LiveCapacity | None = None
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
    fleet = Fleet(inventory.servers, state, LegacyHost(OpenSshTransport()),
                  cooldown_seconds=int(os.environ.get("STORAGE_VIZ_RESCAN_COOLDOWN_SECONDS", "900")),
                  max_active_jobs=int(os.environ.get("STORAGE_VIZ_RESCAN_MAX_CONCURRENT", "2")))
    port = os.environ.get("STORAGE_VIZ_CAPACITY_PORT", "").strip()
    live = LiveCapacity(fleet.servers, int(port)) if port and not auth.PREFLIGHT_BACKEND else None
    return Runtime(fleet, "inventory", inventory.capacity_thresholds, live, not auth.PREFLIGHT_BACKEND,
                   bool(auth.OPERATORS and (auth.TRUSTED_PROXY or auth.DIRECT_LOOPBACK_RESCAN or auth.PREFLIGHT_BACKEND)))


class CentralPoller:
    """Lifecycle-safe background poller for the live capacity feed."""

    def __init__(self, poll: Callable[[], Any], interval_seconds: float) -> None:
        self._poll = poll
        self.interval_seconds = max(float(interval_seconds), 0.001)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def thread_ident(self) -> int | None:
        return self._thread.ident if self._thread else None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="storage-viz-capacity-poller", daemon=True)
            self._thread.start()
            return True

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as exc:
                print(f"storage-viz capacity poll failed: {exc}", file=sys.stderr, flush=True)
            if self._stop.wait(self.interval_seconds):
                break


class Handler(auth.Auth, BaseHTTPRequestHandler):
    def handle_request(self):
        request_id = secrets.token_hex(16)
        try:
            # BaseHTTPRequestHandler rewrites leading slashes. Validate the original target.
            target = self.requestline.split()[1]
            response = routes.dispatch(self, target)
        except Exception as exc:
            status, headers, body = http_error(exc)
            if status == 500:
                logging.exception("storage-viz request %s failed", request_id)
            response = routes.json_response(body, status, headers)
        self.send_response(response.status)
        self.send_header("X-Request-Id", request_id)
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
        poller = CentralPoller(runtime.live_capacity.poll_once, POLL_SECONDS) if runtime.live_capacity else None
        if runtime.runs_actors:
            runtime.fleet.start()
        try:
            if poller:
                poller.start()
            print(f"storage-viz serving {routes.VIEWER_DIR} on {auth.BIND}:{httpd.server_port}", flush=True)
            httpd.serve_forever()
        finally:
            if poller:
                poller.stop()
            if runtime.runs_actors:
                runtime.fleet.stop()


if __name__ == "__main__":
    main()
