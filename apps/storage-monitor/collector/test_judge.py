import pytest

from collector import judge
from collector.contract import Capacity
from collector.inventory import CapacityThresholds


GiB = 1024**3


@pytest.mark.parametrize("pct,free,expected", [
    (79, 512 * GiB + 1, "normal"), (80, 513 * GiB, "warning"),
    (91, 513 * GiB, "warning"), (92, 513 * GiB, "critical"),
    (1, 512 * GiB, "warning"), (1, 128 * GiB + 1, "warning"),
    (1, 128 * GiB, "critical"), (99, None, "unknown"), (None, 0, "unknown"),
])
def test_v2_severity_thresholds(pct, free, expected):
    assert judge.severity(pct, free, CapacityThresholds()) == expected


@pytest.mark.parametrize("used,avail,expected", [(20,80,20),(1,7,13),(0,0,None),(0,20,0)])
def test_used_pct(used, avail, expected):
    fields, _ = judge.volume_capacity(Capacity(used+avail, used, avail, 50, "ok"), 50, CapacityThresholds())
    assert fields["used_pct"] == expected


@pytest.mark.parametrize("elapsed,expected", [(-1, 0), (0, 0), (86399, 0), (86400, 1), (86401, 1)])
def test_v5_age_same_reference(elapsed, expected):
    assert judge.age_days(12345, 12345 + elapsed) == expected


@pytest.mark.parametrize("skew", [-6 * 3600, 0, 6 * 3600])
@pytest.mark.parametrize("cadence", [3600, 10800])
def test_host_clock_skew(skew, cadence):
    capture = 100000 + skew
    deadline = cadence + judge.AGENT_TIMER_INTERVAL_SECONDS + judge.VOLUME_GRACE_SECONDS
    assert judge.volume_freshness(capture, cadence, judge.host_now(None, None, 99)) == "unknown"
    for elapsed, expected in [(deadline, "fresh"), (deadline + 1, "stale")]:
        now = judge.host_now(capture, 99, 99 + elapsed)
        assert judge.volume_freshness(capture, cadence, now) == expected


def test_a_volume_due_just_after_a_timer_tick_is_not_stale_before_the_next_tick():
    # Live: fast volumes showed "오래됨" between hourly runs. A volume that comes due
    # right after a tick is scanned only on the next one, an hour later.
    finished = 100000
    next_tick_scan_done = finished + 3600 + judge.AGENT_TIMER_INTERVAL_SECONDS
    assert judge.volume_freshness(finished, 3600, next_tick_scan_done) == "fresh"


def test_a_volume_being_scanned_is_not_stale():
    finished = 100000
    long_after = finished + 10800 + 6 * 3600
    assert judge.volume_freshness(finished, 10800, long_after) == "stale"
    assert judge.volume_freshness(finished, 10800, long_after, scanning=True) == "fresh"


@pytest.mark.parametrize("observations,agent,expected", [
    ([], None, (None, None)), ([], 0, (0, 0)), ([3, 100, 7], None, (7, 100)),
    ([3, 100, 7], 20, (7, 100)), ([3, 100, 7], 200, (7, 200)),
])
def test_eta(observations, agent, expected):
    assert judge.eta({"full": list(enumerate(observations))}, "full", agent) == expected


@pytest.mark.parametrize("state,elapsed,expected", [
    ("running", 10, "running"), ("running", 11, "unknown"),
    ("complete", 120, "idle"), ("idle", 121, "unknown"), ("unknown", 0, "unknown"),
])
def test_activity_expires(state, elapsed, expected):
    assert judge.activity({"state": state}, 10, 10 + elapsed) == expected
