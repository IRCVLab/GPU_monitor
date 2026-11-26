"""The actor on v2 hosts: one manifest, one immutable capture per volume, and the v1 views built from them."""
import json
import threading

from collector.actor import Admit, Ask
from collector.contract import HostRun
from collector.inventory import CapacityThresholds
from collector.test_bug_harness import Collector, ManualSpawn
from collector.test_contract import capture_doc, encode, gz, manifest_doc, sha, volume_doc
from collector.view import snapshot_v1_chunks

SID = "alpha-1"
VA, BA = "v-000000000000000a", "b-000000000000000a"
VB, BB = "v-000000000000000b", "b-000000000000000b"


def capture(volume_id, binding, root, finished, size=30):
    doc = capture_doc(volume_id, binding, root, started=finished - 100, finished=finished)
    doc["roots"][0]["tree"] = {"name": root.strip("/"), "kind": "dir", "bytes": size, "files": 1, "uid": 0, "mtime": 0,
                               "other_bytes": size - 20, "children": [
                                   {"name": "f", "kind": "file", "bytes": 20, "files": 1, "uid": 1001, "mtime": 0}]}
    doc["roots"][0]["counts"]["bytes"] = size
    return gz(doc)


class V2Host:
    """Writes a v2 host's manifest.json and captures/ onto the fake host."""

    def __init__(self, collector, server_id=SID):
        self.collector = collector
        self.server_id = server_id
        self.seq = 0
        self.volumes = {}

    def volume(self, volume_id, binding, root, data, **fields):
        self.volumes[volume_id] = (binding, root, data, fields)
        if data is not None:
            self.collector.host.volume_captures[(volume_id, sha(data))] = data
        return data

    def publish(self, seq=None, **manifest_fields):
        self.seq = self.seq + 1 if seq is None else seq
        volumes = [volume_doc(volume_id, binding, root, capture=(sha(data), len(data)) if data else None, **fields)
                   for volume_id, (binding, root, data, fields) in self.volumes.items()]
        self.collector.host.manifests[self.server_id] = encode(
            manifest_doc(volumes=volumes, seq=self.seq, server_id=manifest_fields.pop("server_id", self.server_id),
                         **manifest_fields))


def v2_collector(tmp_path, spawn=None):
    collector = Collector(tmp_path, SID, publish=False, **({"spawn": spawn} if spawn else {}))
    host = V2Host(collector)
    finished = collector.clock.now - 600
    host.volume(VA, BA, "/a", capture(VA, BA, "/a", finished))
    host.volume(VB, BB, "/b", capture(VB, BB, "/b", finished), media="ssd", cadence=3600)
    host.publish()
    collector.tick()
    return collector, host


def snapshot(collector, server_id=SID):
    view = collector.fleet.view(server_id)
    return json.loads(b"".join(snapshot_v1_chunks(view.snapshot, None, CapacityThresholds())))


def record(collector, server_id=SID):
    return collector.actor(server_id)._record


def mounts(collector):
    return {mount["mount_id"]: mount for mount in snapshot(collector)["mounts"]}


# ------------------------------------------------------------------ per-volume data


def test_each_volume_is_fetched_once_and_shown_as_one_v1_mount_with_manifest_capacity(tmp_path):
    collector, host = v2_collector(tmp_path)
    for _ in range(5):
        collector.tick()
    assert sorted(volume for _, volume, _ in collector.host.capture_calls) == [VA, VB]
    assert collector.host.manifest_calls == [SID], "an unchanged manifest was read again"
    rows = mounts(collector)
    assert set(rows) == {VA, VB}
    assert (rows[VA]["path"], rows[VA]["df_total"], rows[VA]["df_avail"], rows[VA]["used_pct"]) == ("/a", 1000, 600, 40)
    assert rows[VA]["tree"]["kind"] == "directory" and rows[VA]["tree"]["children"][0]["name"] == "f"
    summary = collector.summary()
    assert (summary["snapshot_availability"], summary["latest_pull_status"], summary["mount_count"]) == ("available", "succeeded", 2)
    assert summary["overview_snapshot"]["users"] == [{"uid": 1001, "name": "kim", "bytes": 0}]
    stored = sorted(p.name for p in (collector.state_dir / "servers" / SID / "captures").iterdir())
    assert stored == sorted(f"{sha(host.volumes[v][2])}.json.gz" for v in (VA, VB)), "captures are not stored byte for byte by sha"
    for volume_id in (VA, VB):
        data = host.volumes[volume_id][2]
        assert (collector.state_dir / "servers" / SID / "captures" / f"{sha(data)}.json.gz").read_bytes() == data


def test_one_rejected_volume_never_blocks_another(tmp_path):
    collector = Collector(tmp_path, SID, publish=False)
    host = V2Host(collector)
    finished = collector.clock.now - 600
    host.volume(VA, BA, "/a", capture(VA, BA, "/a", finished))
    bad = host.volume(VB, BB, "/b", capture(VB, "b-0000000000000099", "/b", finished))
    host.publish()
    collector.tick()
    assert set(mounts(collector)) == {VA}
    held = record(collector).data
    assert held.accepted == {VA: sha(host.volumes[VA][2])}
    assert held.rejected[sha(bad)]["reason"] == "BINDING_MISMATCH" and held.rejected[sha(bad)]["volume_id"] == VB
    roots = {root["mount_id"]: root for root in snapshot(collector)["selected_roots"]}
    assert (roots[VB]["status"], roots[VB]["data_state"]) == ("skipped", "none")


def test_c2_rejected_volume_capture_fetched_once_and_last_good_kept(tmp_path):
    collector, host = v2_collector(tmp_path)
    good = host.volumes[VA][2]
    doc = capture_doc(VA, BA, "/a")
    doc["roots"][0]["tree"]["bytes"] = 5  # no children and no other_bytes, but bytes claimed
    doc["roots"][0]["tree"]["other_bytes"] = 4
    bad = host.volume(VA, BA, "/a", gz(doc))
    host.publish()
    for _ in range(100):
        collector.tick()
    collector.restart()
    collector.tick()
    assert collector.host.captures_of(sha(bad)) == 1, "C2 a rejected capture was fetched again"
    assert record(collector).data.accepted[VA] == sha(good), "the last accepted capture was dropped"
    assert mounts(collector)[VA]["data_state"] == "stale_rejected"
    assert collector.summary()["latest_pull_status"] == "invalid_snapshot"


def test_c8_restart_downloads_nothing_for_v2_captures(tmp_path):
    collector, host = v2_collector(tmp_path)
    captures, manifests = len(collector.host.capture_calls), len(collector.host.manifest_calls)
    before = snapshot(collector)
    collector.restart()
    assert snapshot(collector) == before, "the stored captures were not served from the first read"
    collector.tick()
    assert len(collector.host.capture_calls) - captures == 0, "C8 restart downloaded stored v2 captures"
    assert len(collector.host.manifest_calls) - manifests == 0, "restart read the unchanged manifest again"


def test_capture_gone_rereads_the_manifest_and_does_not_condemn_the_sha(tmp_path):
    spawn = ManualSpawn()
    collector = Collector(tmp_path, SID, publish=False, spawn=spawn)
    host = V2Host(collector)
    finished = collector.clock.now - 600
    data = host.volume(VA, BA, "/a", capture(VA, BA, "/a", finished))
    host.publish()
    collector.tick()
    del collector.host.volume_captures[(VA, sha(data))]
    spawn.run()
    collector.settle()
    assert sha(data) not in record(collector).data.rejected
    assert collector.host.manifest_calls == [SID, SID], "CAPTURE_GONE did not reread the manifest"
    newer = host.volume(VA, BA, "/a", capture(VA, BA, "/a", finished + 10))
    host.publish()
    collector.tick()
    spawn.run()
    collector.settle()
    assert record(collector).data.accepted == {VA: sha(newer)}


def test_capacity_from_a_new_manifest_updates_the_mount_without_a_download(tmp_path):
    collector, host = v2_collector(tmp_path)
    calls = len(collector.host.capture_calls)
    binding, root, data, fields = host.volumes[VA]
    sampled = collector.clock.now
    host.volumes[VA] = (binding, root, data, {**fields, "capacity": {
        "total": 1000, "used": 950, "avail": 50, "observed_unix": sampled, "status": "ok"}})
    host.publish()
    collector.tick()
    assert len(collector.host.capture_calls) == calls
    row = mounts(collector)[VA]
    assert (row["df_used"], row["capacity_observed_unix"], row["used_pct"]) == (950, sampled, 95)


def test_freshness_reads_each_volume_cadence_from_the_manifest(tmp_path):
    collector, host = v2_collector(tmp_path)
    finished = host.collector.clock.now - 61 - 600
    # VB has cadence 3600 and VA 10800, so VB decides. Grace: two idle observes plus the timer delay.
    deadline = finished + 3600 + 2 * 60 + 360
    collector.tick(deadline - collector.clock.now)
    assert collector.summary()["freshness"] == "fresh"
    collector.tick(1)
    assert collector.summary()["freshness"] == "stale"
    binding, root, _, fields = host.volumes[VB]
    host.volume(VB, binding, root, capture(VB, binding, root, collector.clock.now - 10), **fields)
    host.publish()
    collector.tick()
    assert collector.summary()["freshness"] == "fresh"


def test_durations_come_from_complete_or_partial_last_runs_keyed_by_plan(tmp_path):
    collector, host = v2_collector(tmp_path)
    run = {"run_id": "1" * 32, "trigger": "timer", "plan": "ssd", "started_unix": 1000, "finished_unix": 1600,
           "outcome": "complete", "agent_sha": None}
    host.publish(last_run=run)
    collector.tick()
    host.publish(last_run={**run, "started_unix": 2000, "finished_unix": 2100, "outcome": "failed"})
    collector.tick()
    host.publish(last_run={**run, "plan": "full", "started_unix": 3000, "finished_unix": 3400, "outcome": "partial"})
    collector.tick()
    assert record(collector).durations == {"full": [[3400, 400]], "ssd": [[1600, 600]]}


def test_ambiguous_volume_is_shown_with_its_diagnostic_and_never_merged(tmp_path):
    collector = Collector(tmp_path, SID, publish=False)
    host = V2Host(collector)
    finished = collector.clock.now - 600
    ambiguous = "x-00000000000000aa"
    host.volume(VA, BA, "/data", capture(VA, BA, "/data", finished))
    host.volume(ambiguous, "b-00000000000000aa", "/data2", capture(ambiguous, "b-00000000000000aa", "/data2", finished),
                identity="ambiguous", diagnostic="VOLUME_IDENTITY_AMBIGUOUS")
    host.publish()
    collector.tick()
    rows = mounts(collector)
    assert set(rows) == {VA, ambiguous}
    assert (rows[ambiguous]["diagnostic"], rows[VA]["diagnostic"]) == ("VOLUME_IDENTITY_AMBIGUOUS", None)
    assert rows[ambiguous]["path"] != rows[VA]["path"]
    roots = {root["mount_id"]: root for root in collector.summary()["overview_snapshot"]["selected_roots"]}
    assert (roots[ambiguous]["identity"], roots[ambiguous]["diagnostic"]) == ("ambiguous", "VOLUME_IDENTITY_AMBIGUOUS")


# ------------------------------------------------------------------ manifest trust


def test_a_lower_seq_never_replaces_the_held_manifest(tmp_path):
    collector, host = v2_collector(tmp_path)
    held = record(collector).data.manifest
    host.publish(seq=held.seq)  # same seq, different bytes
    host.collector.host.manifests[SID] = host.collector.host.manifests[SID].replace(b'"hostname": "alpha-1"', b'"hostname": "other"')
    for _ in range(3):
        collector.tick()
    assert record(collector).data.manifest == held
    assert (record(collector).link.state, record(collector).link.error) == ("protocol_error", "MANIFEST_SEQ_REGRESSED")
    assert collector.host.manifest_calls.count(SID) == 2, "a refused manifest was read on every observe"


def test_a_manifest_for_another_server_is_an_identity_mismatch(tmp_path):
    collector, host = v2_collector(tmp_path)
    host.publish(server_id="beta-2")
    collector.tick()
    assert record(collector).link.state == "identity_mismatch"
    assert set(mounts(collector)) == {VA, VB}


def test_an_unparsable_manifest_is_a_protocol_error_and_keeps_the_data(tmp_path):
    collector, host = v2_collector(tmp_path)
    collector.host.manifests[SID] = b'{"schema": "storage-viz.manifest/2", "phase": "publishing"}'
    collector.tick()
    assert (record(collector).link.state, record(collector).link.error) == ("protocol_error", "BAD_FIELD")
    assert set(mounts(collector)) == {VA, VB}


# ------------------------------------------------------------------ protocol switches


def test_v1_host_that_gains_a_manifest_moves_off_s3_without_downloading_v1_data(tmp_path):
    collector = Collector(tmp_path, SID)  # a v1 host with one accepted snapshot
    v1_downloads = len(collector.host.fetch_calls)
    collector.host.publish(SID, started=1719203000)  # S8: the v1 files linger, even a newer-looking one
    host = V2Host(collector)
    finished = collector.clock.now - 600
    host.volume(VA, BA, "/a", capture(VA, BA, "/a", finished))
    host.publish()
    for _ in range(3):
        collector.tick()
    assert len(collector.host.fetch_calls) == v1_downloads, "a v1 snapshot was downloaded for a v2 host"
    assert record(collector).data.accepted == {VA: sha(host.volumes[VA][2])}
    assert set(mounts(collector)) == {VA}
    names = [p.name for p in (collector.state_dir / "servers" / SID / "captures").iterdir()]
    assert names == [f"{sha(host.volumes[VA][2])}.json.gz"], "the v1 capture outlived the switch"


def test_v2_host_rolled_back_to_v1_falls_back_to_s3_on_the_retained_v1_files(tmp_path):
    collector, host = v2_collector(tmp_path)
    status, _ = collector.host.publish(SID, started=1719203000)  # the v1 files kept on the host (S8)
    collector.tick()
    assert collector.host.fetches_of(status["sha256"]) == 0
    del collector.host.manifests[SID]
    collector.tick()
    assert collector.host.fetches_of(status["sha256"]) == 1
    assert record(collector).data.accepted == {"legacy": status["sha256"]}
    assert collector.summary()["overview_snapshot"]["scan_generation"] == "alpha-1-1719203000-v1"
    assert record(collector).link.state == "ok"


def test_volume_transfers_share_the_fleet_limit_of_two(tmp_path):
    entered = threading.Semaphore(0)
    release = threading.Event()
    collector = Collector(tmp_path, SID, publish=False,
                          spawn=lambda work, name: threading.Thread(target=work, daemon=True).start())
    host = V2Host(collector)
    finished = collector.clock.now - 600
    for index in range(4):
        volume_id, binding = f"v-{index:016x}", f"b-{index:016x}"
        host.volume(volume_id, binding, f"/d{index}", capture(volume_id, binding, f"/d{index}", finished))
    host.publish()

    def gate(server):
        entered.release()
        assert release.wait(5), "transfer barrier was never released"

    collector.host.fetch_gate = gate
    collector.tick()
    assert entered.acquire(timeout=5) and entered.acquire(timeout=5), "two transfers should run"
    assert collector.host.active_fetches == 2, "a third volume transfer ran past the fleet bound"
    release.set()
    actor = collector.actor()
    while len(record(collector).data.accepted) < 4:
        actor._guard(actor._handle, actor.inbox.get(timeout=5))
    assert collector.host.max_active_fetches == 2


# ------------------------------------------------------------------ receipt jobs (C4 central side)


def admit(collector):
    ask = Ask()
    collector.actor().post(Admit("e" * 32, "operator", collector.clock.now, 900, ask))
    collector.actor().step()
    return ask.value


def test_v2_rescan_is_confirmed_only_by_the_manifest_receipt(tmp_path):
    collector, host = v2_collector(tmp_path)
    reply = admit(collector)
    assert reply.job.protocol == "receipt"
    collector.settle()
    assert collector.host.rescan_requests == [(SID, "e" * 32)]
    assert collector.summary()["active_job"]["phase"] == "pending"
    collector.host.runs[SID] = HostRun(present=True, scanning=True, run_id="9" * 32, pending=frozenset({"e" * 32}))
    for _ in range(5):
        collector.tick(5)
    assert collector.summary()["active_job"]["phase"] == "pending", "C4 a scheduled scan moved the waiting request"
    assert collector.host.rescan_requests == [(SID, "e" * 32)], "the request was resent while a scan held the lock"
    collector.host.runs[SID] = HostRun(present=True, scanning=True, run_id="8" * 32, request_ids=frozenset({"e" * 32}))
    collector.tick(5)
    assert collector.summary()["active_job"]["phase"] == "running"
    collector.host.runs[SID] = HostRun()
    binding, root, _, fields = host.volumes[VA]
    host.volume(VA, binding, root, capture(VA, binding, root, collector.clock.now - 5), **fields)
    host.publish(requests=[{"request_id": "e" * 32, "run_id": "8" * 32, "received_unix": 0, "outcome": "complete"}])
    collector.tick(5)
    job = collector.actor()._record.last_job
    assert (job.state, job.result_code, job.request_id) == ("succeeded", "OK", "e" * 32)
    assert collector.actor()._record.data.accepted[VA] == sha(host.volumes[VA][2])


def test_v2_interrupted_receipt_fails_the_job(tmp_path):
    collector, host = v2_collector(tmp_path)
    admit(collector)
    collector.settle()
    host.publish(requests=[{"request_id": "e" * 32, "run_id": "8" * 32, "received_unix": 0, "outcome": "interrupted"}])
    collector.tick(5)
    job = collector.actor()._record.last_job
    assert (job.state, job.result_code) == ("failed", "SCAN_INTERRUPTED")


def test_legacy_adapter_refuses_a_request_id_it_cannot_deliver(tmp_path):
    from collector.legacy import LegacyHost
    from collector.test_bug_harness import make_server
    reply = LegacyHost(transport=None).rescan(make_server(), "e" * 32)
    assert (reply.outcome, reply.code) == ("rejected", "REQUEST_UNSUPPORTED")
