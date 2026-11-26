import pytest

from collector.test_bug_harness import Collector
from collector.transport import TransportError


@pytest.mark.xfail(
    strict=True, raises=pytest.RaisesExc(AssertionError, match="^C1 "),
    reason='C1 "연결 실패" badge stays up to 15 min while telemetry and data are current',
)
def test_c1_observe_success_clears_unreachable(tmp_path):
    host = Collector(tmp_path, "alpha-1")
    host.transport.status_errors["alpha-1"] = TransportError("TIMEOUT", "timed out")
    assert host.service.poll_once()["alpha-1"]["latest_pull_status"] == "unreachable"
    host.transport.status_errors.clear()
    host.tick()

    summary, = host.service.server_summaries()
    assert summary["scan_status"]["observed_unix"] == host.clock.now
    assert summary["latest_pull_status"] == "succeeded", "C1 successful observation left the host unreachable"
