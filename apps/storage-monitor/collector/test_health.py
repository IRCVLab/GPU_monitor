from collector import health
from collector.health import (
    CaptureAccepted, CaptureMismatch, CaptureRejected, Data, ExchangeFailed, ExchangeOk, Fetch, Link,
    PublicationObserved, TransferFailed, data_state, reduce_data, reduce_link,
)
from tests.machine_check import assert_table_total, run_random_events

SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


def envelope(sha, status="complete"):
    return {"generation": f"alpha-1-{ord((sha or 'x')[0])}-v1.json", "sha256": sha, "status": status}


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
    data, effects = reduce_data(Data(), PublicationObserved(envelope(SHA_A)))
    assert effects == (Fetch(envelope(SHA_A)),) and data_state(data) == "none"
    again, effects = reduce_data(data, PublicationObserved(envelope(SHA_A)))
    assert effects == (), "a capture already in flight was fetched twice"
    data, _ = reduce_data(again, CaptureAccepted(SHA_A))
    assert (data.accepted, data_state(data)) == (SHA_A, "current")
    assert reduce_data(data, PublicationObserved(envelope(SHA_A))) == (data, ())


def test_data_rejected_capture_is_never_fetched_again_and_keeps_last_good():
    data, _ = reduce_data(Data(accepted=SHA_A), PublicationObserved(envelope(SHA_B)))
    data, _ = reduce_data(data, CaptureRejected(SHA_B, "alpha-1-98-v1", "INVALID", at=5))
    assert (data.accepted, data_state(data)) == (SHA_A, "stale_rejected")
    for _ in range(100):
        data, effects = reduce_data(data, PublicationObserved(envelope(SHA_B)))
        assert effects == ()
    data, effects = reduce_data(data, PublicationObserved(envelope(SHA_C)))
    assert effects == (Fetch(envelope(SHA_C)),) and SHA_B not in data.rejected


def test_data_mismatch_gets_one_clean_retry_then_is_rejected():
    data, _ = reduce_data(Data(), PublicationObserved(envelope(SHA_A)))
    data, _ = reduce_data(data, CaptureMismatch(SHA_A, None, at=1))
    data, effects = reduce_data(data, PublicationObserved(envelope(SHA_A)))
    assert effects == (Fetch(envelope(SHA_A)),)
    data, _ = reduce_data(data, CaptureMismatch(SHA_A, None, at=2))
    assert data.rejected[SHA_A]["reason"] == "SHA_MISMATCH"
    assert reduce_data(data, PublicationObserved(envelope(SHA_A)))[1] == ()


def test_data_failed_status_keeps_capture_and_fetches_nothing():
    data, effects = reduce_data(Data(accepted=SHA_A), PublicationObserved(envelope(None, "failed")))
    assert effects == () and data_state(data) == "current" and data.manifest["status"] == "failed"


def test_data_table_total():
    assert_table_total(health.DATA_TRANSITIONS, health.DATA_STATES, health.DATA_EVENT_KINDS)


def test_data_random_events():
    shas = (SHA_A, SHA_B, SHA_C, None)

    def event(rng, data, index):
        sha = rng.choice(shas[:3])
        kind = rng.randrange(5)
        if kind == 0:
            pick = rng.choice(shas)
            return PublicationObserved(envelope(pick, rng.choice(("complete", "partial", "failed"))) if pick else None)
        if kind == 1:
            return CaptureAccepted(data.fetching if rng.random() < 0.7 and data.fetching else sha)
        if kind == 2:
            return CaptureRejected(sha, None, "INVALID", at=index)
        if kind == 3:
            return CaptureMismatch(data.fetching or sha, None, at=index)
        return TransferFailed(data.fetching or sha)

    def check(before, event, after, effects):
        assert after.accepted not in after.rejected, "a sha is both accepted and rejected"
        assert len(after.rejected) <= health.MAX_REJECTED
        assert len(effects) <= 1
        for fetch in effects:
            sha = fetch.manifest["sha256"]
            assert sha not in before.rejected and sha != before.accepted, "fetched a rejected or held capture"
            assert before.fetching is None, "two transfers in flight"
            assert after.fetching == sha
        assert data_state(after) in health.DATA_STATES

    run_random_events(lambda rng: Data(), event, reduce_data, check)
