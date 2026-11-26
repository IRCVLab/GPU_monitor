"""Collector fixtures: a real on-disk store, a fake clock and a simulated host on the legacy protocol.

``Collector`` drives the actors without threads. ``settle`` handles every queued
message and runs each contact that is due on the fake clock, so a test controls
time and ordering exactly.
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import threading

from collector.contract import AgentFacts, HostRun, ManifestPointer
from collector.fleet import Fleet
from collector.inventory import Server
from collector.jobs import RescanReply, Scan
from collector.legacy import Observation, empty_status
from collector.update import Attempt
from collector.test_snapshot import base_payload, status_for
from collector.transport import TransportError
from collector.view import summary_v1


def make_server(server_id="alpha-1", order=1, enabled=True, digest="a" * 64):
    return Server(
        id=server_id, display_name=server_id, order=order, host=f"{server_id}.example.test", port=22,
        enabled=enabled, username="monitoring",
        identity_file=pathlib.PurePosixPath(f"/etc/storage-viz/{server_id}.key"),
        known_hosts_file=pathlib.PurePosixPath("/etc/storage-viz/known_hosts"),
        scanner={"server_id": server_id}, scanner_digest=digest,
    )


def payload_for(server_id="alpha-1", started=1719200000, digest="a" * 64, partial=False, total_failed=False):
    payload = base_payload(server_id=server_id, generation=f"{server_id}-{started}-v1")
    payload.update(hostname=f"{server_id}.example.test", scan_started_unix=started,
                   scan_finished_unix=started + 42, config_digest=digest)
    if partial:
        payload["selected_roots"].append({
            "mount_id": "archive", "major_minor": "8:2", "mount_source": "/dev/storage-viz/archive",
            "mount_root": "/", "mountpoint": "/archive", "scan_root": "/archive", "fstype": "xfs",
            "status": "failed", "scanned_bytes": 0, "scanned_files": 0, "scanned_dirs": 0,
            "blocked_count": 0, "error_count": 1, "error_code": "EIO",
        })
    if total_failed:
        payload["selected_roots"][0].update(status="failed", scanned_bytes=0, scanned_files=0, scanned_dirs=0, error_count=1, error_code="EIO")
        payload["mounts"] = []
    return payload


def status_data(payload, status_value=None):
    status, data = status_for(payload, config_digest=payload["config_digest"])
    if status_value is not None:
        status["status"] = status_value
    elif any(root["status"] == "failed" for root in payload["selected_roots"]):
        status["status"] = "partial"
    return status, data


class FakeClock:
    def __init__(self, now=1719200042 + 3600):
        self.now = now

    def time(self):
        return self.now

    def monotonic(self):
        return self.now


def inline(work, name):
    work()


class ManualSpawn:
    """Holds helper work until the test runs it, to put a transfer or a rescan start in flight."""

    def __init__(self):
        self.pending = []

    def __call__(self, work, name):
        self.pending.append((name, work))

    def run(self, prefix=""):
        ready = [item for item in self.pending if item[0].startswith(prefix)]
        self.pending = [item for item in self.pending if item not in ready]
        for _, work in ready:
            work()
        return len(ready)


class FakeHost:
    """A host as the legacy adapter reports it: published status, captures, scan units."""

    def __init__(self, clock):
        self.clock = clock
        self.publications = {}
        self.captures = {}
        self.scans = {}
        self.statuses = {}
        self.observe_errors = {}
        self.fetch_errors = {}
        self.rescan_errors = {}
        self.observe_calls = []
        self.fetch_calls = []
        self.rescans = []
        self.observe_gate = None
        self.fetch_gate = None
        self.rescan_started = threading.Event()
        self.lock = threading.Lock()
        self.active_fetches = 0
        self.max_active_fetches = 0
        # v2 hosts
        self.manifests = {}
        """server_id -> manifest.json bytes. A server with one is a v2 host."""
        self.volume_captures = {}
        """(volume_id, sha256) -> gzip bytes under captures/."""
        self.runs = {}
        self.manifest_calls = []
        self.capture_calls = []
        self.manifest_errors = {}
        self.rescan_requests = []
        self.delivers_request_ids = True
        """Observations come over the gate; False makes them S2 observations, which deliver no request id."""
        self.rescan_replies = {}
        # the agent update channel
        self.agents = {}
        """server_id -> FakeUpdater: the release the gate reports and what its updater answers."""
        self.update_calls = []
        """(server_id, verb, sha) for every stage, activate and rollback request."""
        self.update_errors = {}

    def publish(self, server_id, started=1719200000, **kwargs):
        status, data = status_data(payload_for(server_id, started=started, **kwargs))
        self.publications[server_id] = status
        self.captures[status["sha256"]] = data
        return status, data

    def observe(self, server):
        self.observe_calls.append(server.id)
        if self.observe_gate is not None:
            self.observe_gate(server)
        error = self.observe_errors.get(server.id)
        if error is not None:
            raise error
        status = {**empty_status(), "state": "idle", **self.statuses.get(server.id, {})}
        publication = self.publications.get(server.id)
        manifest = self.manifests.get(server.id)
        pointer = None
        if manifest is not None:
            pointer = ManifestPointer(hashlib.sha256(manifest).hexdigest(), json.loads(manifest).get("seq"))
        agent = self.agents.get(server.id)
        return Observation(int(self.clock.time()), status, copy.deepcopy(publication), None, self.scans.get(server.id, Scan()),
                           pointer, self.runs.get(server.id, HostRun()),
                           transport="gate" if self.delivers_request_ids else "legacy",
                           agent=agent.facts() if agent is not None else AgentFacts())

    def stage_update(self, server, sha, bundle, signature):
        self._update_call(server, "stage-update", sha)
        self.agents[server.id].staged[sha] = (bundle, signature)

    def activate_update(self, server, sha):
        self._update_call(server, "activate-update", sha)
        self.agents[server.id].request("activate", sha, int(self.clock.time()))

    def rollback_update(self, server, sha):
        self._update_call(server, "rollback-update", sha)
        self.agents[server.id].request("rollback", sha, int(self.clock.time()))

    def _update_call(self, server, verb, sha):
        self.update_calls.append((server.id, verb, sha))
        error = self.update_errors.get((server.id, verb))
        if error is not None:
            raise error

    def get_manifest(self, server):
        self.manifest_calls.append(server.id)
        error = self.manifest_errors.get(server.id)
        if error is not None:
            raise error
        if server.id not in self.manifests:
            raise TransportError("MANIFEST_MISSING", "missing")
        return self.manifests[server.id]

    def get_capture(self, server, volume_id, sha256, cap):
        with self.lock:
            self.capture_calls.append((server.id, volume_id, sha256))
            self.active_fetches += 1
            self.max_active_fetches = max(self.max_active_fetches, self.active_fetches)
        try:
            if self.fetch_gate is not None:
                self.fetch_gate(server)
            data = self.volume_captures.get((volume_id, sha256))
            if data is None:
                raise TransportError("CAPTURE_GONE", "missing")
            if len(data) > cap:
                raise TransportError("CAPTURE_TOO_LARGE", "too large")
            return data
        finally:
            with self.lock:
                self.active_fetches -= 1

    def rescan(self, server, request_id):
        self.rescan_requests.append((server.id, request_id))
        reply = self.rescan_replies.get(server.id, RescanReply("received"))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def captures_of(self, sha):
        return sum(1 for _, _, fetched in self.capture_calls if fetched == sha)

    def fetch_capture(self, server, publication):
        with self.lock:
            self.fetch_calls.append((server.id, publication["sha256"]))
            self.active_fetches += 1
            self.max_active_fetches = max(self.max_active_fetches, self.active_fetches)
        try:
            if self.fetch_gate is not None:
                self.fetch_gate(server)
            error = self.fetch_errors.get(server.id)
            if error is not None:
                raise error
            return self.captures[publication["sha256"]]
        finally:
            with self.lock:
                self.active_fetches -= 1

    def start_rescan(self, server):
        self.rescans.append(server.id)
        self.rescan_started.set()
        error = self.rescan_errors.get(server.id)
        if error is not None:
            raise error

    def fetches_of(self, sha):
        return sum(1 for _, fetched in self.fetch_calls if fetched == sha)


class FakeUpdater:
    """The host's storage-viz-update as the gate reports it: it answers a request at once."""

    def __init__(self, release):
        self.release = release
        self.previous = None
        self.broken = False
        self.bad = set()
        self.staged = {}
        self.attempt = None
        self.answers = {}
        """sha -> (outcome, code) to answer an activation of that sha with, in place of activating it."""

    def request(self, action, sha, now):
        if action == "rollback":
            if sha != self.previous:
                self.attempt = Attempt("rollback", sha, "refused", "ROLLBACK_TARGET", now)
                return
            self.bad.add(self.release)
            self.release, self.previous = sha, None
            self.attempt = Attempt("rollback", sha, "rolled_back", None, now)
            return
        outcome, code = self.answers.get(sha, (None, None))
        if outcome is None and sha in self.bad:
            outcome, code = "refused", "BAD_RELEASE"
        if outcome is None and sha not in self.staged:
            outcome, code = "failed", "BUNDLE_MISSING"
        if outcome is not None:
            self.attempt = Attempt("activate", sha, outcome, code, now)
            return
        self.previous, self.release = self.release, sha
        self.attempt = Attempt("activate", sha, "activated", None, now)

    def facts(self):
        return AgentFacts(release_sha=self.release, broken=self.broken, attempt=self.attempt)


class Collector:
    def __init__(self, tmp_path, *ids, publish=True, spawn=inline, **fleet_options):
        self.fleet_options = fleet_options
        self.state_dir = tmp_path / "state"
        self.clock = FakeClock()
        self.host = FakeHost(self.clock)
        self.spawn = spawn
        self.servers = [make_server(sid, order=i) for i, sid in enumerate(ids, 1)]
        if publish:
            for sid in ids:
                self.host.publish(sid)
        self.restart()
        self.settle()
        if publish:
            assert all(self.summary(sid)["latest_pull_status"] == "succeeded" for sid in ids)

    def restart(self, **fleet_options):
        fleet_options = {"desired_path": self.state_dir.parent / "no-agent.json", **self.fleet_options, **fleet_options}
        self.fleet = Fleet(self.servers, self.state_dir, self.host, clock=self.clock, spawn=self.spawn, **fleet_options)
        for actor in self.fleet.actors.values():
            actor._guard(actor.load)

    def settle(self):
        for actor in self.fleet.actors.values():
            actor.step()
            while not actor.inbox.empty():
                actor.step()

    def tick(self, seconds=61):
        self.clock.now += seconds
        self.settle()

    def actor(self, server_id="alpha-1"):
        return self.fleet.actors[server_id]

    def summary(self, server_id="alpha-1"):
        return summary_v1(self.fleet.view(server_id), self.clock.monotonic())


def transport_error(code):
    return TransportError(code, code.lower())
