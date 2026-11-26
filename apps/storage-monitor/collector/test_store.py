import hashlib
import json
import os
from unittest import mock

import pytest

from collector.errors import StoreUnavailable
from collector.health import LEGACY_VOLUME, Data, LegacyPublication, Link
from collector.jobs import Job
from collector.store import Record, ServerStore

CAPTURE = b'{"schema_version":1}'
SHA = hashlib.sha256(CAPTURE).hexdigest()


def record(**data):
    return Record(
        link=Link(state="ok", seq=7, since=1, last_ok_unix=2),
        data=Data(manifest=LegacyPublication({"sha256": SHA, "status": "complete"}), accepted={LEGACY_VOLUME: SHA}, **data),
        last_job=Job("q" * 32, "operator", 10, 20, state="succeeded", finished_unix=20, result_code="OK"),
        durations={"full": [[100, 30]]},
    )


def test_commit_then_load_round_trips_and_names_the_capture_by_its_sha256(tmp_path):
    store = ServerStore(tmp_path, "alpha-1")
    assert store.load() is None
    store.commit(record(), (LEGACY_VOLUME, SHA, CAPTURE))
    assert ServerStore(tmp_path, "alpha-1").load() == record()
    path = tmp_path / "servers" / "alpha-1" / "captures" / f"{SHA}.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SHA
    assert store.read_capture(LEGACY_VOLUME, SHA) == CAPTURE


def test_transient_data_fields_are_not_persisted(tmp_path):
    store = ServerStore(tmp_path, "alpha-1")
    store.commit(record(fetching=frozenset({"f" * 64}), retried=frozenset({"e" * 64})), (LEGACY_VOLUME, SHA, CAPTURE))
    assert store.load() == record()


def test_commit_keeps_only_the_capture_the_record_names(tmp_path):
    store = ServerStore(tmp_path, "alpha-1")
    store.commit(record(), (LEGACY_VOLUME, SHA, CAPTURE))
    newer = b'{"schema_version":1,"x":1}'
    newer_sha = hashlib.sha256(newer).hexdigest()
    store.commit(Record(data=Data(accepted={LEGACY_VOLUME: newer_sha})), (LEGACY_VOLUME, newer_sha, newer))
    assert sorted(p.name for p in store.captures.iterdir()) == [f"{newer_sha}.json"]


@pytest.mark.parametrize("change", [
    lambda raw: raw.update(freshness="fresh"),
    lambda raw: raw.update(schema="storage-viz.server-record/1"),
    lambda raw: raw.update(server_id="beta-2"),
    lambda raw: raw["link"].update(extra=1),
    lambda raw: raw.update(accepted={"legacy": "nothex"}),
    lambda raw: raw.update(durations={"full": [[5, 1], [4, 1]]}),
])
def test_records_with_derived_unknown_or_foreign_fields_are_unavailable(tmp_path, change):
    store = ServerStore(tmp_path, "alpha-1")
    store.commit(record(), (LEGACY_VOLUME, SHA, CAPTURE))
    raw = json.loads(store.record_path.read_text())
    change(raw)
    store.record_path.write_text(json.dumps(raw))
    with pytest.raises(StoreUnavailable):
        store.load()


ACTIVE_JOB = {"request_id": "q" * 32, "actor": "operator", "requested_unix": 10, "since_unix": 10, "state": "running",
              "dispatches": 1, "baseline_run": None, "invocation": "i" * 32, "agent_run": None, "generation": None,
              "finished_unix": None, "result_code": None}


@pytest.mark.parametrize("change", [
    lambda raw: raw.update(job={**ACTIVE_JOB, "requested_unix": "10"}),
    lambda raw: raw.update(job={**ACTIVE_JOB, "dispatches": True}),
    lambda raw: raw.update(job={**ACTIVE_JOB, "state": "queued"}),
    lambda raw: raw.update(job={**ACTIVE_JOB, "state": "succeeded"}),
    lambda raw: raw.update(job={**ACTIVE_JOB, "invocation": 7}),
    lambda raw: raw["last_job"].update(state="running"),
    lambda raw: raw["last_job"].update(finished_unix=[20]),
    lambda raw: raw["link"].update(seq="7"),
    lambda raw: raw["link"].update(state="sideways"),
    lambda raw: raw["update"].update(attempts=None),
])
def test_records_with_malformed_job_link_or_update_fields_are_unavailable(tmp_path, change):
    # M2b review: field types and states were not checked, so a bad record raised on every request instead.
    store = ServerStore(tmp_path, "alpha-1")
    store.commit(record(), (LEGACY_VOLUME, SHA, CAPTURE))
    raw = json.loads(store.record_path.read_text())
    change(raw)
    store.record_path.write_text(json.dumps(raw))
    with pytest.raises(StoreUnavailable):
        store.load()


def test_a_well_formed_active_job_round_trips(tmp_path):
    store = ServerStore(tmp_path, "alpha-1")
    active = Record(job=Job(**ACTIVE_JOB))
    store.commit(active)
    assert store.load() == active


@pytest.mark.parametrize("job", [
    {"id": "rescan-alpha-1-1-1", "actor": "op", "requested_unix": "soon", "state": "succeeded"},
    {"id": "rescan-alpha-1-1-1", "actor": "op", "requested_unix": 1719200000, "state": "queued"},
    {"id": "rescan-alpha-1-1-1", "actor": "op", "requested_unix": 1719200000, "state": "failed", "result_code": 3},
])
def test_a_malformed_old_job_is_not_imported_into_the_record(tmp_path, job):
    old = tmp_path / "alpha-1"
    old.mkdir()
    state = "state-v2-" + "0" * 32 + ".json"
    (old / state).write_text(json.dumps({"active_job": job}))
    (old / "current.json").write_text(json.dumps({"snapshot": None, "state": state}))
    store = ServerStore(tmp_path, "alpha-1")
    imported = store.import_legacy()
    if imported is not None:
        store.commit(*imported)
        assert store.load().last_job is None


def test_truncated_or_symlinked_record_is_unavailable(tmp_path):
    store = ServerStore(tmp_path, "alpha-1")
    store.commit(record(), (LEGACY_VOLUME, SHA, CAPTURE))
    store.record_path.write_text("{")
    with pytest.raises(StoreUnavailable):
        store.load()
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    store.record_path.unlink()
    store.record_path.symlink_to(target)
    with pytest.raises(StoreUnavailable):
        store.load()


def test_failed_write_keeps_the_previous_record_and_leaves_no_temp_file(tmp_path):
    store = ServerStore(tmp_path, "alpha-1")
    store.commit(record(), (LEGACY_VOLUME, SHA, CAPTURE))
    before = store.record_path.read_bytes()
    with mock.patch("collector.store.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(StoreUnavailable):
            store.commit(Record())
    assert store.record_path.read_bytes() == before
    assert not [p for p in store.directory.rglob("*.tmp")]


def test_unsafe_server_ids_and_capture_names_are_refused(tmp_path):
    for bad in ("../x", ".", "a/b", ""):
        with pytest.raises(ValueError):
            ServerStore(tmp_path, bad)
    store = ServerStore(tmp_path, "alpha-1")
    with pytest.raises(StoreUnavailable):
        store.commit(Record(), (LEGACY_VOLUME, "../escape", b"x"))
    assert not os.path.exists(tmp_path / "servers" / "escape")
