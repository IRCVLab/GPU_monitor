import pytest

from collector.test_bug_harness import Collector


@pytest.mark.xfail(
    strict=True, raises=pytest.RaisesExc(AssertionError, match="^C2 "),
    reason='C2 A rejected newer generation is re-downloaded every telemetry tick (up to 512 MB per download)',
)
def test_c2_rejected_capture_fetched_once(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.transport.publish("alpha-1", started=1719203000)
    status, data = host.transport.downloads["alpha-1"]
    host.transport.downloads["alpha-1"] = status, data[:-1] + b" "
    for _ in range(100):
        host.tick()
    assert host.store.load_state("alpha-1")["latest_pull_status"] == "invalid_snapshot"
    host.restart()
    host.tick()
    attempts = sum(request["sha256"] == status["sha256"]
                   for _, request in host.transport.fetch_snapshot_calls)
    assert attempts == 1, f"C2 rejected capture fetched {attempts} times across 100 ticks and restart"


@pytest.mark.xfail(
    strict=True, raises=pytest.RaisesExc(AssertionError, match="^C8 "),
    reason='C8 Every dashboard restart re-downloads every snapshot it already holds',
)
def test_c8_restart_downloads_nothing(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    before = len(host.transport.fetch_snapshot_calls)
    host.restart()
    assert host.service.poll_once()["alpha-1"]["latest_pull_status"] == "succeeded"
    downloads = len(host.transport.fetch_snapshot_calls) - before
    assert downloads == 0, f"C8 restarted collector downloaded {downloads} unchanged snapshots"
