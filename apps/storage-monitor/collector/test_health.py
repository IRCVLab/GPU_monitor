from collector import health
from collector.contract import parse_manifest
from collector.health import (
    CaptureAccepted, CaptureGone, CaptureMismatch, CaptureRejected, Data, ExchangeFailed, ExchangeOk,
    Fetch, Link, PublicationObserved, TransferFailed, data_state, reduce_data, reduce_link,
    volume_state,
)
from collector.test_contract import encode, manifest_doc, volume_doc
from tests.machine_check import assert_table_total, run_random_events

SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


VA, VB = "v-000000000000000a", "v-000000000000000b"


def manifest(a=None, b=None, seq=1):
    """A v2 manifest naming volumes VA and VB with the given capture shas."""
    return parse_manifest(encode(manifest_doc(seq=seq, volumes=[
        volume_doc(VA, "b-000000000000000a", "/a", capture=(a, 10) if a else None),
        volume_doc(VB, "b-000000000000000b", "/b", capture=(b, 10) if b else None),
    ])))


def test_c1_observe_success_clears_unreachable():
    link = reduce_link(Link(), ExchangeFailed(seq=1, at=100, code="TIMEOUT"))
    assert link.state == "unreachable"
    link = reduce_link(link, ExchangeOk(seq=2, at=105))
    assert link.state == "ok", "C1 a newer successful observe left the link unreachable"
    assert (link.last_ok_unix, link.error) == (105, None)


def test_c1_late_failure_ignored():
    # A capture transfer issued as exchange 7 times out after observe 8 succeeded.
    link = reduce_link(Link(), ExchangeOk(seq=8, at=200))
    late = reduce_link(link, ExchangeFailed(seq=7, at=260, code="TIMEOUT"))
    assert late == link, "C1 a late failure for an older exchange marked a reachable host unreachable"


def test_link_failure_kinds_map_to_states():
    for code, state in (("UNREACHABLE", "unreachable"), ("AUTH_FAILED", "auth_failed"),
                        ("BAD_TELEMETRY", "protocol_error"), ("IDENTITY_MISMATCH", "identity_mismatch")):
        assert reduce_link(Link(), ExchangeFailed(seq=1, at=1, code=code)).state == state
    link = reduce_link(Link(), ExchangeFailed(seq=1, at=1, code="TIMEOUT"))
    assert reduce_link(link, ExchangeFailed(seq=2, at=9, code="TIMEOUT")).since == 1


def test_link_table_total():
    assert_table_total(health.LINK_TRANSITIONS, health.LINK_STATES, health.LINK_EVENT_KINDS)


def test_link_random_events():
    codes = ("TIMEOUT", "UNREACHABLE", "AUTH_FAILED", "BAD_STATUS", "IDENTITY_MISMATCH")

    def event(rng, link, index):
        seq = rng.randint(max(0, link.seq - 3), link.seq + 3)
        if rng.random() < 0.5:
            return ExchangeOk(seq=seq, at=index)
        return ExchangeFailed(seq=seq, at=index, code=rng.choice(codes))

    def check(before, event, after, effects):
        assert after.seq >= before.seq, "link.seq decreased"
        if event.seq <= before.seq:
            assert after == before, "an older result changed the link"
        assert after.state in health.LINK_STATES
        if after.state == "ok":
            assert after.error is None and after.last_ok_unix is not None

    run_random_events(lambda rng: Link(), event, lambda link, e: (reduce_link(link, e), ()), check)


def test_data_fetches_a_new_publication_once_and_accepts_it():
    data, effects = reduce_data(Data(), PublicationObserved(manifest(SHA_A)))
    assert effects == (Fetch(VA, SHA_A, manifest(SHA_A)),) and data_state(data) == "none"
    again, effects = reduce_data(data, PublicationObserved(manifest(SHA_A)))
    assert effects == (), "a capture already in flight was fetched twice"
    data, _ = reduce_data(again, CaptureAccepted(VA, SHA_A))
    assert (data.accepted, data_state(data)) == ({VA: SHA_A}, "current")
    assert reduce_data(data, PublicationObserved(manifest(SHA_A))) == (data, ())


def test_data_rejected_capture_is_never_fetched_again_and_keeps_last_good():
    data, _ = reduce_data(Data(accepted={VA: SHA_A}), PublicationObserved(manifest(SHA_B)))
    data, _ = reduce_data(data, CaptureRejected(VA, SHA_B, "INVALID", at=5))
    assert (data.accepted, data_state(data)) == ({VA: SHA_A}, "stale_rejected")
    for _ in range(100):
        data, effects = reduce_data(data, PublicationObserved(manifest(SHA_B)))
        assert effects == ()
    data, effects = reduce_data(data, PublicationObserved(manifest(SHA_C)))
    assert effects == (Fetch(VA, SHA_C, manifest(SHA_C)),) and SHA_B not in data.rejected


def test_data_mismatch_gets_one_clean_retry_then_is_rejected():
    data, _ = reduce_data(Data(), PublicationObserved(manifest(SHA_A)))
    data, _ = reduce_data(data, CaptureMismatch(VA, SHA_A, at=1))
    data, effects = reduce_data(data, PublicationObserved(manifest(SHA_A)))
    assert effects == (Fetch(VA, SHA_A, manifest(SHA_A)),)
    data, _ = reduce_data(data, CaptureMismatch(VA, SHA_A, at=2))
    assert data.rejected[SHA_A]["reason"] == "SHA_MISMATCH"
    assert reduce_data(data, PublicationObserved(manifest(SHA_A)))[1] == ()


def test_data_failed_status_keeps_capture_and_fetches_nothing():
    data, effects = reduce_data(Data(accepted={VA: SHA_A}), PublicationObserved(manifest(None)))
    assert effects == () and data_state(data) == "current"


def test_data_fetches_each_volume_and_one_rejection_does_not_block_another():
    m = manifest(SHA_A, SHA_B)
    data, effects = reduce_data(Data(), PublicationObserved(m))
    assert {(f.volume_id, f.sha) for f in effects} == {(VA, SHA_A), (VB, SHA_B)}
    data, _ = reduce_data(data, CaptureRejected(VA, SHA_A, "TREE_SUM_MISMATCH", at=1))
    data, _ = reduce_data(data, CaptureAccepted(VB, SHA_B))
    assert data.accepted == {VB: SHA_B}
    assert (volume_state(data, VA), volume_state(data, VB)) == ("none", "current")
    assert data.rejected[SHA_A] == {"volume_id": VA, "reason": "TREE_SUM_MISMATCH", "at": 1}
    assert reduce_data(data, PublicationObserved(m))[1] == (), "a rejected capture was fetched again"


def test_data_volume_with_a_rejected_new_capture_keeps_its_last_accepted_sha():
    data = Data(manifest=manifest(SHA_A, SHA_B), accepted={VA: SHA_A, VB: SHA_B})
    data, effects = reduce_data(data, PublicationObserved(manifest(SHA_C, SHA_B, seq=2)))
    assert [(f.volume_id, f.sha) for f in effects] == [(VA, SHA_C)]
    data, _ = reduce_data(data, CaptureRejected(VA, SHA_C, "BINDING_MISMATCH", at=3))
    assert data.accepted == {VA: SHA_A, VB: SHA_B}
    assert (volume_state(data, VA), volume_state(data, VB), data_state(data)) == ("stale_rejected", "current", "stale_rejected")


def test_data_drops_volumes_and_rejections_the_manifest_no_longer_names():
    data = Data(manifest=manifest(SHA_A, SHA_B), accepted={VA: SHA_A, "v-000000000000000c": SHA_C},
                rejected={SHA_B: {"volume_id": VB, "reason": "X", "at": 0}, SHA_C: {"volume_id": VB, "reason": "X", "at": 0}})
    data, _ = reduce_data(data, PublicationObserved(manifest(SHA_A, SHA_B)))
    assert data.accepted == {VA: SHA_A}, "the removed volume retained its capture"
    assert set(data.rejected) == {SHA_B}


def test_data_capture_for_a_superseded_sha_is_not_accepted():
    data, _ = reduce_data(Data(), PublicationObserved(manifest(SHA_A)))
    data, _ = reduce_data(data, PublicationObserved(manifest(SHA_B, seq=2)))
    data, _ = reduce_data(data, CaptureAccepted(VA, SHA_A))
    assert data.accepted == {} and SHA_A not in data.fetching


def test_data_table_total():
    assert_table_total(health.DATA_TRANSITIONS, health.DATA_STATES, health.DATA_EVENT_KINDS)


def test_data_random_events():
    shas = (SHA_A, SHA_B, SHA_C, None)
    volumes = (VA, VB)
    manifests = {(a, b): manifest(a, b) for a in shas for b in shas}

    def event(rng, data, index):
        sha = rng.choice(shas[:3])
        volume = rng.choice(volumes)
        if data.fetching and rng.random() < 0.7:
            sha = rng.choice(sorted(data.fetching))
        kind = rng.randrange(6)
        if kind == 5:
            return CaptureGone(volume, sha, at=index * 30)
        if kind == 0:
            return PublicationObserved(manifests[(rng.choice(shas), rng.choice(shas))], index * 30)
        if kind == 1:
            return CaptureAccepted(volume, sha)
        if kind == 2:
            return CaptureRejected(volume, sha, "INVALID", at=index)
        if kind == 3:
            return CaptureMismatch(volume, sha, at=index)
        return TransferFailed(volume, sha)

    def check(before, event, after, effects):
        assert not any(after.rejected.get(sha, {}).get("volume_id") == volume_id for volume_id, sha in after.accepted.items()), \
            "a volume's held sha is also rejected for that volume"
        assert len(after.rejected) <= health.MAX_REJECTED
        for fetch in effects:
            assert fetch.sha not in before.rejected, "fetched a rejected capture"
            assert fetch.sha != before.accepted.get(fetch.volume_id), "fetched a held capture"
            assert fetch.sha not in before.fetching, "two transfers of one capture"
            assert fetch.sha in after.fetching
            assert before.gone.get(fetch.sha, (0, 0))[1] <= event.at, "fetched a lost capture before its back-off ended"
        if isinstance(event, CaptureAccepted) and after.accepted != before.accepted:
            assert event.sha in before.fetching and health.targets(after)[event.volume_id] == event.sha
        if after.manifest is not None:
            assert set(after.accepted) <= set(after.manifest.targets()), "held a volume the manifest does not name"
        assert data_state(after) in health.DATA_STATES

    run_random_events(lambda rng: Data(), event, reduce_data, check)
