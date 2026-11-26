"""The v2 boundary parsers: the §2.2 examples parse, and every rejection has its own reason."""
import gzip
import hashlib
import json

import pytest

from collector import contract
from collector.contract import ContractError, parse_capture, parse_manifest

SID = "s13"
VID = "v-3f9a0c1d2e4b5a69"
BINDING = "b-77c1aa00bb11cc22"
RUN = "1" * 32
REQUEST = "2" * 32
AGENT_SHA = "3" * 40


def capture_doc(volume_id=VID, binding=BINDING, scan_root="/data", started=1764100000, finished=1764103400):
    """The §2.2 VolumeCapture example, with its placeholders filled."""
    return {
        "schema": "storage-viz.capture/2",
        "volume_id": volume_id,
        "run_id": RUN, "group": "slow",
        "scan_started_unix": started, "scan_finished_unix": finished,
        "outcome": "partial",
        "roots": [{
            "scan_root": scan_root, "binding": binding, "prune_class": "data", "outcome": "partial",
            "counts": {"bytes": 0, "files": 0, "dirs": 0, "errors": 1, "vanished_dirs": 1},
            "tree": {"name": scan_root.strip("/") or "/", "kind": "dir", "bytes": 0, "files": 0, "uid": 0, "mtime": 0,
                     "other_bytes": 0, "children": []},
            "users": [{"uid": 1001, "name": "kim", "bytes": 0, "files": 0}],
            "top_files": [{"path": f"{scan_root}/a/b.bin", "bytes": 0, "uid": 1001, "mtime": 0}],
            "stale": [{"path": f"{scan_root}/c", "kind": "dir", "bytes": 0, "uid": 1001, "mtime": 0}],
            "errors": [{"path": f"{scan_root}/x", "reason": "VANISHED"}],
        }],
    }


def gz(doc):
    return gzip.compress(json.dumps(doc, sort_keys=True).encode(), mtime=0)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def volume_doc(volume_id=VID, binding=BINDING, scan_root="/data", capture=None, identity="uuid", diagnostic=None,
               cadence=10800, capacity=None, media="hdd"):
    """One §2.2 manifest volume. ``capture`` is (sha256, byte count) or None."""
    return {
        "volume_id": volume_id, "identity": identity, "diagnostic": diagnostic,
        "mountpoint": scan_root, "fstype": "ext4", "media": media, "media_confidence": "resolved",
        "group": "slow", "cadence_sec": cadence,
        "roots": [{"scan_root": scan_root, "binding": binding, "prune_class": "data"}],
        "capacity": capacity or {"total": 1000, "used": 400, "avail": 600, "observed_unix": 1764103404, "status": "ok"},
        "capture": None if capture is None else {
            "sha256": capture[0], "bytes": capture[1], "scan_started_unix": 1764100000,
            "scan_finished_unix": 1764103400, "outcome": "partial"},
        "last_attempt": {"run_id": RUN, "started_unix": 1764100000, "finished_unix": 1764103400,
                         "outcome": "failed", "error_code": "SCANNER_FAILED"},
    }


def manifest_doc(volumes=None, seq=1842, server_id=SID, requests=None, last_run=None):
    """The §2.2 HostManifest example, with its placeholders filled."""
    if volumes is None:
        data = gz(capture_doc())
        volumes = [volume_doc(capture=(sha(data), len(data)))]
    return {
        "schema": "storage-viz.manifest/2",
        "server_id": server_id, "hostname": server_id, "seq": seq, "written_unix": 1764103405,
        "agent": {"release_sha": AGENT_SHA, "release_seq": 913, "config_digest": "a" * 64},
        "volumes": volumes,
        "mount_table": {"status": "ok", "observed_unix": 0,
                        "within_roots": [{"mountpoint": "/data/scratch", "same_device": False},
                                         {"mountpoint": "/data/shared", "same_device": True}]},
        "last_run": last_run if last_run is not None else {
            "run_id": RUN, "trigger": "timer", "plan": "full", "started_unix": 0,
            "finished_unix": 0, "outcome": "partial", "agent_sha": AGENT_SHA},
        "requests": requests if requests is not None else [
            {"request_id": REQUEST, "run_id": RUN, "received_unix": 0, "outcome": "complete"}],
    }


def encode(doc):
    return json.dumps(doc, sort_keys=True).encode()


def expected(doc=None, volume=None):
    """What the volume of ``manifest_doc()`` expects of the gzip bytes of ``doc``."""
    data = gz(doc or capture_doc())
    manifest = parse_manifest(encode(manifest_doc(volumes=[volume or volume_doc(capture=(sha(data), len(data)))])))
    return data, manifest.volumes[0].expected(sha(data))


def reason_of(call):
    with pytest.raises(ContractError) as info:
        call()
    return info.value.reason


# ------------------------------------------------------------------ valid input


def test_manifest_written_as_the_design_shows_parses():
    data = encode(manifest_doc())
    manifest = parse_manifest(data)
    volume = manifest.volumes[0]
    assert (manifest.server_id, manifest.seq, manifest.sha256) == (SID, 1842, sha(data))
    assert (volume.volume_id, volume.cadence_sec, volume.capacity.avail, volume.capture.outcome) == (VID, 10800, 600, "partial")
    assert manifest.targets() == {VID: volume.capture.sha256}
    assert [(r.request_id, r.outcome) for r in manifest.requests] == [(REQUEST, "complete")]
    assert (manifest.last_run.plan, manifest.last_run.outcome) == ("full", "partial")
    assert manifest.raw == json.loads(data), "the record keeps the manifest verbatim"


def test_capture_written_as_the_design_shows_parses():
    data, want = expected()
    capture = parse_capture(data, want)
    root = capture.roots[0]
    assert (capture.volume_id, capture.outcome, capture.scan_finished_unix) == (VID, "partial", 1764103400)
    assert (root.binding, root.counts["vanished_dirs"], root.errors[0]["reason"]) == (BINDING, 1, "VANISHED")


def test_folded_children_count_in_other_bytes_and_files_roll_up():
    doc = capture_doc()
    doc["roots"][0]["tree"] = {"name": "data", "kind": "directory", "bytes": 30, "files": 3, "uid": 0, "mtime": 0,
                               "other_bytes": 10, "children": [
                                   {"name": "a", "kind": "file", "bytes": 20, "files": 1, "uid": 1001, "mtime": 0}]}
    data, want = expected(doc)
    assert parse_capture(data, want).roots[0].tree["bytes"] == 30


def chain(depth):
    node = {"name": f"n{depth}", "kind": "file", "bytes": 1, "files": 1, "uid": 0, "mtime": 0}
    for level in range(depth - 1, -1, -1):
        node = {"name": f"n{level}", "kind": "dir", "bytes": 1, "files": 1, "uid": 0, "mtime": 0, "children": [node]}
    return node


def test_a_tree_at_the_depth_cap_parses():
    doc = capture_doc()
    doc["roots"][0]["tree"] = chain(contract.MAX_TREE_DEPTH)
    data, want = expected(doc)
    assert parse_capture(data, want).roots[0].tree["name"] == "n0"


# ------------------------------------------------------------------ rejections

MANIFEST_CASES = {
    "too large": (contract.MANIFEST_TOO_LARGE, lambda: encode(manifest_doc()) + b" " * contract.MAX_MANIFEST_BYTES),
    "not json": (contract.MALFORMED, lambda: b"{"),
    "not an object": (contract.MALFORMED, lambda: b"[]"),
    "unknown schema": (contract.BAD_SCHEMA, lambda: encode({**manifest_doc(), "schema": "storage-viz.manifest/3"})),
    "phase key (I12)": (contract.BAD_FIELD, lambda: encode({**manifest_doc(), "phase": "publishing"})),
    "missing key": (contract.BAD_FIELD, lambda: encode({k: v for k, v in manifest_doc().items() if k != "requests"})),
    "wrong type": (contract.BAD_FIELD, lambda: encode(manifest_doc(volumes=[{**volume_doc(), "cadence_sec": "3h"}]))),
    "server id": (contract.BAD_ID, lambda: encode(manifest_doc(server_id="s 13"))),
    "volume id": (contract.BAD_ID, lambda: encode(manifest_doc(volumes=[volume_doc(volume_id="v-NOTHEX")]))),
    "binding": (contract.BAD_ID, lambda: encode(manifest_doc(volumes=[volume_doc(binding="b-77c1")]))),
    "x- id without ambiguity": (contract.BAD_ID, lambda: encode(manifest_doc(volumes=[volume_doc(volume_id="x-" + "0" * 16)]))),
    "request id": (contract.BAD_ID, lambda: encode(manifest_doc(requests=[
        {"request_id": "nothex", "run_id": None, "received_unix": 0, "outcome": "complete"}]))),
    "seq string": (contract.BAD_SEQ, lambda: encode(manifest_doc(seq="1842"))),
    "seq bool": (contract.BAD_SEQ, lambda: encode(manifest_doc(seq=True))),
    "seq float": (contract.BAD_SEQ, lambda: encode(manifest_doc(seq=1842.0))),
    "duplicate volume": (contract.DUPLICATE_ID, lambda: encode(manifest_doc(volumes=[
        volume_doc(), volume_doc(binding="b-0000000000000001", scan_root="/other")]))),
    "duplicate scan root": (contract.DUPLICATE_ID, lambda: encode(manifest_doc(volumes=[
        volume_doc(), volume_doc(volume_id="v-0000000000000001", binding="b-0000000000000001")]))),
}


@pytest.mark.parametrize("case", sorted(MANIFEST_CASES))
def test_manifest_rejection_reasons(case):
    reason, make = MANIFEST_CASES[case]
    assert reason_of(lambda: parse_manifest(make())) == reason


def _capture_case(change=None, volume=None, max_json_bytes=contract.MAX_CAPTURE_JSON_BYTES, raw=None):
    def run():
        doc = capture_doc()
        if change is not None:
            change(doc)
        data = raw if raw is not None else gz(doc)
        manifest = parse_manifest(encode(manifest_doc(volumes=[volume or volume_doc(capture=(sha(data), len(data)))])))
        return parse_capture(data, manifest.volumes[0].expected(sha(data)), max_json_bytes=max_json_bytes)
    return run


def _sum_mismatch(doc):
    doc["roots"][0]["tree"] = {"name": "data", "kind": "dir", "bytes": 31, "files": 1, "uid": 0, "mtime": 0,
                               "other_bytes": 10, "children": [
                                   {"name": "a", "kind": "file", "bytes": 20, "files": 1, "uid": 0, "mtime": 0}]}


def _files_mismatch(doc):
    doc["roots"][0]["tree"] = {"name": "data", "kind": "dir", "bytes": 20, "files": 0, "uid": 0, "mtime": 0,
                               "children": [{"name": "a", "kind": "file", "bytes": 20, "files": 1, "uid": 0, "mtime": 0}]}


def _complete_with_errors(doc):
    doc["outcome"] = doc["roots"][0]["outcome"] = "complete"


CAPTURE_CASES = {
    "inflates past the limit": (contract.CAPTURE_TOO_LARGE, _capture_case(max_json_bytes=100)),
    "not gzip": (contract.MALFORMED, _capture_case(raw=b"plain bytes")),
    "gzip of non-json": (contract.MALFORMED, _capture_case(raw=gzip.compress(b"{", mtime=0))),
    "unknown schema": (contract.BAD_SCHEMA, _capture_case(lambda doc: doc.update(schema="storage-viz.capture/1"))),
    "wrong type": (contract.BAD_FIELD, _capture_case(lambda doc: doc["roots"][0]["counts"].update(bytes=-1))),
    "unknown node kind": (contract.BAD_FIELD, _capture_case(lambda doc: doc["roots"][0]["tree"].update(kind="socket"))),
    "row outside its root": (contract.BAD_FIELD, _capture_case(lambda doc: doc["roots"][0]["top_files"][0].update(path="/etc/x"))),
    "run id": (contract.BAD_ID, _capture_case(lambda doc: doc.update(run_id="r1"))),
    "wrong volume": (contract.VOLUME_MISMATCH, _capture_case(lambda doc: doc.update(volume_id="v-0000000000000001"))),
    "binding not in the manifest": (contract.BINDING_MISMATCH, _capture_case(
        lambda doc: doc["roots"][0].update(binding="b-0000000000000009"))),
    "root missing": (contract.BINDING_MISMATCH, _capture_case(lambda doc: doc.update(roots=[]))),
    "bytes do not add up": (contract.TREE_SUM_MISMATCH, _capture_case(_sum_mismatch)),
    "files do not roll up": (contract.TREE_SUM_MISMATCH, _capture_case(_files_mismatch)),
    "deeper than the cap (A10)": (contract.TREE_TOO_DEEP, _capture_case(
        lambda doc: doc["roots"][0].update(tree=chain(contract.MAX_TREE_DEPTH + 1)))),
    "complete root with errors": (contract.OUTCOME_MISMATCH, _capture_case(_complete_with_errors)),
    "volume outcome better than its roots": (contract.OUTCOME_MISMATCH, _capture_case(lambda doc: doc.update(outcome="complete"))),
}


@pytest.mark.parametrize("case", sorted(CAPTURE_CASES))
def test_capture_rejection_reasons(case):
    reason, run = CAPTURE_CASES[case]
    assert reason_of(run) == reason


def test_capture_bytes_that_differ_from_the_manifest_sha_are_a_mismatch():
    data, want = expected()
    assert reason_of(lambda: parse_capture(data + b"\0", want)) == contract.SHA_MISMATCH
    assert reason_of(lambda: parse_capture(data[:-4], want)) == contract.SHA_MISMATCH


def test_every_rejection_reason_has_a_case():
    covered = {reason for reason, _ in MANIFEST_CASES.values()} | {reason for reason, _ in CAPTURE_CASES.values()}
    assert covered | {contract.SHA_MISMATCH} == contract.REASONS


def test_an_ambiguous_volume_parses_with_its_diagnostic():
    volume = volume_doc(volume_id="x-" + "a" * 16, identity="ambiguous", diagnostic="VOLUME_IDENTITY_AMBIGUOUS")
    manifest = parse_manifest(encode(manifest_doc(volumes=[volume])))
    assert (manifest.volumes[0].identity, manifest.volumes[0].diagnostic) == ("ambiguous", "VOLUME_IDENTITY_AMBIGUOUS")

