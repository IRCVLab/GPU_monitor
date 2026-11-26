import pytest

from collector import judge
from collector.contract import snapshot_response
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


@pytest.mark.parametrize("mount,expected", [
    ({"df_total": 100, "df_used": 20, "df_use_pct": 85}, 85),
    ({"df_total": 8, "df_used": 1}, 13),
    ({"df_total": 0, "df_used": 0}, None),
    ({"df_total": 0, "df_used": 0, "df_use_pct": 0}, 0),
    ({"df_total": 2**53, "df_used": 1}, None),
    ({"df_total": True, "df_used": 1}, None),
])
def test_used_pct(mount, expected):
    assert judge.used_pct(mount) == expected


@pytest.mark.parametrize("elapsed,expected", [(-1, 0), (0, 0), (86399, 0), (86400, 1), (86401, 1)])
def test_v5_age_same_reference(elapsed, expected):
    row = {"mtime": 12345}
    snapshot = {"scan_started_unix": 12345 + elapsed, "top_files": [row], "stale": [dict(row, age_days=999)]}
    response = snapshot_response(snapshot)
    assert [response[table][0]["age_days"] for table in ("top_files", "stale")] == [expected, expected]
    assert "age_days" not in row
    assert snapshot["stale"][0]["age_days"] == 999


@pytest.mark.parametrize("skew", [-6 * 3600, 0, 6 * 3600])
@pytest.mark.parametrize("media,deadline", [(None, 7 * 3600), ("ssd", 4860), ("hdd", 12060), ("unknown", 12060)])
def test_host_clock_skew(skew, media, deadline):
    capture = 100000 + skew
    snapshot = {"scan_finished_unix": capture}
    if media:
        snapshot["selected_roots"] = [{"scan_finished_unix": capture, "storage_media": media}]
    assert judge.freshness(snapshot, judge.host_now(None, None, 99), 900) == "unknown"
    for elapsed, expected in [(deadline, "fresh"), (deadline + 1, "stale")]:
        now = judge.host_now(capture, 99, 99 + elapsed)
        assert judge.freshness(snapshot, now, 900) == expected


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
