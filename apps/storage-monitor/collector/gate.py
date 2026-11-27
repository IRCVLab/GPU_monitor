"""The host interface over the gate (design §4.5), and each server's choice of transport.

``GateHost`` makes the calls the actor makes (``observe``, ``get_manifest``,
``get_capture``, ``rescan`` and the update verbs) as gate verbs through
``GateTransport.run_verb``. ``parse_observe`` is the parse boundary for the
gate's ``observe`` JSON: it maps it into the ``Observation`` the reducers read.

``HostRouter`` picks the transport per server, explicitly (``link.transport``):

- inventory ``transport: gate``: every verb goes over the gate, with no S2 fallback;
- inventory ``transport: legacy``, the default: S2, until the gate answers an ``observe`` with its
  header, then the gate. The gate is tried on the first contact and after each failed
  S2 observe, because a bootstrap turns S2 off for the host. A reply without the gate
  header (NOT_GATE) on such a host means the bootstrap was reverted: back to S2. A
  timeout or refused login decides nothing.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Mapping

from collector import contract, legacy
from collector.contract import ContractError
from collector.health import AUTH_CODES, TRANSPORT_CODES
from collector.inventory import Server
from collector.jobs import RescanReply
from collector.transport import (
    NOT_GATE, GateTransport, TransportError, status_tuple, validate_snapshot_status, validate_status_envelope,
)

SERVICE_UNITS = (legacy.MANUAL_UNIT, legacy.SCHEDULED_UNIT)
"""The units of the gate's ``legacy.services`` blocks, in its order."""


def parse_observe(raw: bytes, server: Server) -> legacy.Observation:
    """The gate's ``observe`` reply as an Observation, or TransportError BAD_TELEMETRY.

    ``contract.parse_observation`` reads the bytes. The S7 ``legacy`` section then goes
    through the v1 telemetry parser, so a v1 agent behind the gate reads as it does over S2.
    """
    try:
        facts = contract.parse_observation(raw)
    except ContractError as exc:
        raise TransportError("BAD_TELEMETRY", f"gate observe reply refused: {exc.reason}") from exc
    section = facts.legacy
    try:
        probe = {
            "lifecycle": section["lifecycle"], "published": section["published"], "progress": section["progress"],
            "services": _services(section["services"], facts.units), "timer": section["timer"],
            "remote_unix": section["remote_unix"], "remote_monotonic": section["remote_monotonic"],
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise TransportError("BAD_TELEMETRY", "gate observe legacy section is malformed") from exc
    observation = legacy.parse_telemetry(probe, server, lambda unit_active: (facts.manifest, facts.run, facts.progress))
    # The run facts come from the lock and run.json for a v1 agent too: the update track stages only on an idle host.
    return legacy.Observation(observation.host_now, observation.status, observation.publication,
                              observation.publication_error, observation.scan, observation.manifest,
                              facts.run, transport="gate", agent=facts.agent)


def _services(services: Any, units: Mapping[str, Any]) -> list:
    """The ``systemctl show`` blocks, named: the gate reports Id and InvocationID under ``units``."""
    if isinstance(services, dict):  # the gate shrank it to fit its cap
        return []
    blocks = []
    for name, block in zip(SERVICE_UNITS, services):
        unit = units.get(name) or {}
        invocation = unit.get("InvocationID") or ""
        blocks.append(f"{block}\nId={name}\nInvocationID={invocation}")
    return blocks


class GateHost:
    """The host interface (``actor.Host``) over the gate."""

    def __init__(self, transport: GateTransport) -> None:
        self.transport = transport

    def observe(self, server: Server) -> legacy.Observation:
        return parse_observe(self.transport.run_verb(server, "observe"), server)

    def get_manifest(self, server: Server) -> bytes:
        return self.transport.run_verb(server, "get-manifest")

    def get_capture(self, server: Server, volume_id: str, sha256: str, cap: int) -> bytes:
        return self.transport.run_verb(server, "get-capture", (volume_id, sha256), cap=cap)

    def rescan(self, server: Server, request_id: str) -> RescanReply:
        body = self.transport.run_verb(server, "rescan", (request_id,))
        words = body.decode("ascii", "replace").split()
        if words in (["received"], ["already_received"]):
            return RescanReply(words[0])
        if len(words) == 2 and words[0] == "rejected":
            return RescanReply("rejected", words[1][:64])
        raise TransportError("BAD_REPLY", "unparsable rescan reply")

    def stage_update(self, server: Server, sha: str, bundle: bytes, signature: bytes) -> None:
        self._expect(self.transport.run_verb(server, "stage-update", (sha, str(len(bundle))), stdin=bundle + signature),
                     b"staged\n")

    def activate_update(self, server: Server, sha: str) -> None:
        self._expect(self.transport.run_verb(server, "activate-update", (sha,)), b"requested\n")

    def rollback_update(self, server: Server, sha: str) -> None:
        self._expect(self.transport.run_verb(server, "rollback-update", (sha,)), b"requested\n")

    @staticmethod
    def _expect(body: bytes, expected: bytes) -> None:
        if body != expected:
            raise TransportError("BAD_REPLY", "unexpected gate reply")

    def fetch_capture(self, server: Server, publication: Mapping[str, Any]) -> bytes:
        """SHIM(M3b..M5c) S7: the v1 snapshot a scan-status envelope names, checked against a second status."""
        generation = validate_snapshot_status(publication, server, 512 * 1024 * 1024)
        data = self.transport.run_verb(server, "get-snapshot", (generation,), cap=publication["byte_size"])
        try:
            second = validate_status_envelope(json.loads(self.transport.run_verb(server, "get-status")), server)
        except ValueError as exc:
            raise TransportError("MALFORMED_STATUS", "status JSON is malformed") from exc
        if status_tuple(publication) != status_tuple(second):
            raise TransportError("RACE", "status changed during snapshot download")
        return data

    def start_rescan(self, server: Server) -> None:
        """SHIM(M3b..M5c) S4: a v1 agent reads no request ids, but ``rescan`` still starts its scan unit."""
        reply = self.rescan(server, uuid.uuid4().hex)
        if reply.outcome == "rejected":
            raise TransportError(reply.code or "RESCAN_FAILED", "the gate did not start the scan")


class HostRouter:
    """The host interface for the whole fleet: each call goes to the server's chosen transport.

    Each server's choice is written only by its own actor thread, in ``observe``.
    """

    def __init__(self, gate: GateHost, legacy_host: Any) -> None:
        self.gate = gate
        self.legacy = legacy_host
        self._on_gate: Dict[str, bool] = {}
        self._probe_gate: Dict[str, bool] = {}

    def transport(self, server: Server) -> str:
        return "gate" if server.transport == "gate" or self._on_gate.get(server.id) else "legacy"

    def _host(self, server: Server) -> Any:
        return self.gate if self.transport(server) == "gate" else self.legacy

    def observe(self, server: Server) -> legacy.Observation:
        if server.transport == "gate":
            return self.gate.observe(server)
        if self._on_gate.get(server.id) or self._probe_gate.get(server.id, True):
            try:
                observation = self.gate.observe(server)
            except TransportError as exc:
                if exc.code in TRANSPORT_CODES | AUTH_CODES:
                    raise  # no answer: nothing shows which transport the host speaks
                if exc.code != NOT_GATE:
                    self._on_gate[server.id] = True  # the gate answered, with a refusal or a bad reply
                    raise
                self._on_gate[server.id] = False
            else:
                self._on_gate[server.id] = True
                return observation
        self._probe_gate[server.id] = False
        try:
            return self.legacy.observe(server)
        except TransportError:
            self._probe_gate[server.id] = True
            raise

    def get_manifest(self, server: Server) -> bytes:
        return self._host(server).get_manifest(server)

    def get_capture(self, server: Server, volume_id: str, sha256: str, cap: int) -> bytes:
        return self._host(server).get_capture(server, volume_id, sha256, cap)

    def rescan(self, server: Server, request_id: str) -> RescanReply:
        return self._host(server).rescan(server, request_id)

    def fetch_capture(self, server: Server, publication: Mapping[str, Any]) -> bytes:
        return self._host(server).fetch_capture(server, publication)

    def start_rescan(self, server: Server) -> None:
        self._host(server).start_rescan(server)

    def stage_update(self, server: Server, sha: str, bundle: bytes, signature: bytes) -> None:
        self.gate.stage_update(server, sha, bundle, signature)

    def activate_update(self, server: Server, sha: str) -> None:
        self.gate.activate_update(server, sha)

    def rollback_update(self, server: Server, sha: str) -> None:
        self.gate.rollback_update(server, sha)
