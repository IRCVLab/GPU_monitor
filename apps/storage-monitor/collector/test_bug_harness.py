"""Collector fixtures: a real on-disk store, a fake clock and a simulated host on the gate protocol.

``Collector`` drives the actors without threads. ``settle`` handles every queued
message and runs each contact that is due on the fake clock, so a test controls
time and ordering exactly.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import threading

from collector.contract import AgentFacts, HostRun, ManifestPointer
from collector.fleet import Fleet
from collector.inventory import Server
from collector.jobs import RescanReply
from collector.gate import Observation
from collector.update import Attempt
from collector.transport import TransportError
from collector.view import server_bytes, activity_body
from collector.inventory import CapacityThresholds


def make_server(server_id="alpha-1", order=1, enabled=True, digest="a" * 64):
    return Server(
        id=server_id, display_name=server_id, order=order, host=f"{server_id}.example.test", port=22,
        enabled=enabled, username="monitoring",
        identity_file=pathlib.PurePosixPath(f"/etc/storage-viz/{server_id}.key"),
        known_hosts_file=pathlib.PurePosixPath("/etc/storage-viz/known_hosts"),
        scanner={"server_id": server_id}, scanner_digest=digest,
    )


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
    """A gate host with published manifests, captures and scan requests."""

    def __init__(self, clock):
        self.clock = clock
        self.statuses = {}
        self.observe_errors = {}
        self.fetch_errors = {}
        self.observe_calls = []
        self.observe_gate = None
        self.fetch_gate = None
        self.lock = threading.Lock()
        self.active_fetches = 0
        self.max_active_fetches = 0
        self.manifests = {}
        """server_id -> manifest.json bytes. A server with one is a v2 host."""
        self.volume_captures = {}
        """(volume_id, sha256) -> gzip bytes under captures/."""
        self.runs = {}
        self.manifest_calls = []
        self.capture_calls = []
        self.manifest_errors = {}
        self.rescan_requests = []
        self.rescan_replies = {}
        # the agent update channel
        self.agents = {}
        """server_id -> FakeUpdater: the release the gate reports and what its updater answers."""
        self.update_calls = []
        """(server_id, verb, sha) for every stage, activate and rollback request."""
        self.update_errors = {}

    def publish(self, server_id, started=1719200000):
        from collector.test_contract import VID, capture_doc, encode, gz, manifest_doc, sha, volume_doc
        data = gz(capture_doc(started=started, finished=started + 42))
        volume = volume_doc(capture=(sha(data), len(data)))
        volume["capture"].update(scan_started_unix=started, scan_finished_unix=started + 42)
        doc = manifest_doc(server_id=server_id, seq=started, volumes=[volume])
        self.manifests[server_id] = encode(doc)
        self.volume_captures[VID, sha(data)] = data
        return volume["capture"], data

    def observe(self, server):
        self.observe_calls.append(server.id)
        if self.observe_gate is not None:
            self.observe_gate(server)
        error = self.observe_errors.get(server.id)
        if error is not None:
            raise error
        status = {"state": "idle", **self.statuses.get(server.id, {})}
        manifest = self.manifests.get(server.id)
        pointer = None
        if manifest is not None:
            pointer = ManifestPointer(hashlib.sha256(manifest).hexdigest(), json.loads(manifest).get("seq"))
        agent = self.agents.get(server.id)
        return Observation(int(self.clock.time()), status, pointer, self.runs.get(server.id, HostRun()),
                           agent.facts() if agent is not None else AgentFacts())

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
            if server.id in self.fetch_errors:
                raise self.fetch_errors[server.id]
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
            assert all(self.summary(sid)["link"]["state"] == "ok" for sid in ids)

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
        return json.loads(server_bytes(self.fleet.view(server_id), self.clock.monotonic(), CapacityThresholds()))

    def latest_job(self, server_id="alpha-1"):
        state = activity_body(self.fleet.view(server_id), self.clock.monotonic())
        return state["job"] or state["last_job"]


def transport_error(code):
    return TransportError(code, code.lower())
