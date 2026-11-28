import logging
import os

import pytest

from collector.routeros import RouterOSError
from collector.switch_traffic import (LinkSpec, NetworkConfig, STATE_FILE, SwitchTraffic, parse_config)

SPEC = LinkSpec("nexus", "bond_nas", 200_000)
CONFIG = NetworkConfig("192.0.2.1", 8728, "storage-viz", os.path.abspath("secret"), (SPEC,))
GB = 125_000_000  # bytes per second at 1 Gb/s


class Clock:
    def __init__(self, unix=1_000_000_020.0):
        self.unix = unix

    def time(self):
        return self.unix

    def monotonic(self):
        return self.unix - 1_000_000_000

    def advance(self, seconds):
        self.unix += seconds


class Client:
    """Switch counters: ``tx`` is what the switch sent to Nexus."""
    def __init__(self, switch):
        self.switch, self.closed = switch, False

    def talk(self, words):
        if self.switch.fail:
            raise self.switch.fail
        return [{"name": "other", "rx-byte": "1", "tx-byte": "1"},
                {"name": "bond_nas", "rx-byte": str(self.switch.rx), "tx-byte": str(self.switch.tx)}]

    def close(self):
        self.closed = True


class Switch:
    def __init__(self):
        self.rx = self.tx = 0
        self.fail = None
        self.connects = 0

    def connect(self):
        self.connects += 1
        return Client(self)


def make(tmp_path=None, **kw):
    clock, switch = Clock(), Switch()
    return SwitchTraffic(CONFIG, tmp_path, clock, switch.connect, **kw), clock, switch


def sample(traffic, clock, switch, *, to_nexus_gbps=0.0, from_nexus_gbps=0.0, seconds=2):
    clock.advance(seconds)
    switch.tx += int(to_nexus_gbps * GB * seconds)
    switch.rx += int(from_nexus_gbps * GB * seconds)
    return traffic.poll_once()


def link(traffic):
    return traffic.snapshot()["links"][0]


def test_rates_are_from_nexus_perspective_and_the_first_sample_only_sets_the_baseline():
    traffic, clock, switch = make()
    assert sample(traffic, clock, switch) == 2
    assert link(traffic)["now"] is None
    sample(traffic, clock, switch, to_nexus_gbps=22, from_nexus_gbps=3)
    assert link(traffic)["now"] == {"rx_bps": 22_000_000_000, "tx_bps": 3_000_000_000, "unix": 1_000_000_024}


def test_a_counter_reset_and_a_long_gap_leave_holes_without_a_bogus_rate():
    traffic, clock, switch = make()
    sample(traffic, clock, switch)
    sample(traffic, clock, switch, to_nexus_gbps=10)
    switch.tx = 5
    sample(traffic, clock, switch)
    assert link(traffic)["now"]["rx_bps"] == 10_000_000_000 and link(traffic)["buckets"][0][2] == 10_000_000_000
    sample(traffic, clock, switch, to_nexus_gbps=5, seconds=11)
    assert link(traffic)["buckets"][0][2] == 10_000_000_000 and link(traffic)["now"]["unix"] == 1_000_000_024
    sample(traffic, clock, switch, to_nexus_gbps=5)
    assert link(traffic)["now"]["rx_bps"] == 5_000_000_000


def test_buckets_hold_per_minute_average_and_max_and_stay_within_24_hours():
    traffic, clock, switch = make()
    sample(traffic, clock, switch)  # baseline at :22
    for gbps in (2, 6, 4):  # :24 :26 :28 all in minute 1_000_000_020
        sample(traffic, clock, switch, to_nexus_gbps=gbps)
    assert link(traffic)["buckets"] == [[1_000_000_020, 4_000_000_000, 6_000_000_000, 0, 0]]
    for _ in range(24 * 360 + 30):
        sample(traffic, clock, switch, to_nexus_gbps=1, seconds=10)
    view = link(traffic)
    assert len(view["buckets"]) <= 24 * 60 + 1
    assert view["buckets"][0][0] > clock.unix - 24 * 3600 - 60
    assert view["peak"]["rx_bps"] == 1_000_000_000


def test_peak_is_the_24_hour_maximum_with_its_minute_and_ages_out():
    traffic, clock, switch = make()
    sample(traffic, clock, switch)
    sample(traffic, clock, switch, to_nexus_gbps=20, from_nexus_gbps=1)
    peak_minute = link(traffic)["peak"]["rx_unix"]
    clock.advance(600)
    sample(traffic, clock, switch)
    sample(traffic, clock, switch, to_nexus_gbps=3, from_nexus_gbps=7)
    peak = link(traffic)["peak"]
    assert (peak["rx_bps"], peak["rx_unix"], peak["tx_bps"]) == (20_000_000_000, peak_minute, 7_000_000_000)
    clock.advance(24 * 3600 + 700)
    assert link(traffic)["peak"] is None and link(traffic)["buckets"] == []


def test_state_is_ok_then_stale_after_30_seconds_and_error_while_the_switch_fails():
    traffic, clock, switch = make()
    assert link(traffic)["state"] == "stale"
    sample(traffic, clock, switch)
    sample(traffic, clock, switch, to_nexus_gbps=1)
    assert link(traffic)["state"] == "ok"
    clock.advance(31)
    assert link(traffic)["state"] == "stale"
    switch.fail = OSError("down")
    traffic.poll_once()
    assert link(traffic)["state"] == "error"


def test_reconnects_with_capped_backoff_after_an_error_and_recovers():
    traffic, clock, switch = make()
    sample(traffic, clock, switch)
    first = traffic._client
    switch.fail = RouterOSError("connection closed by the switch")
    delays = [sample(traffic, clock, switch) for _ in range(8)]
    assert delays == [2, 4, 8, 16, 30, 30, 30, 30] and first.closed
    switch.fail = None
    assert sample(traffic, clock, switch) == 2
    assert traffic._client is not first
    sample(traffic, clock, switch, to_nexus_gbps=1)
    assert link(traffic)["state"] == "ok"


def test_a_missing_interface_marks_only_the_link_in_error():
    traffic, clock, switch = make()
    traffic._links["nexus:bond_nas"].spec = LinkSpec("nexus", "gone", 1)
    sample(traffic, clock, switch)
    assert link(traffic)["state"] == "error"


def test_history_survives_a_restart_and_is_written_at_most_once_a_minute(tmp_path):
    traffic, clock, switch = make(tmp_path)
    sample(traffic, clock, switch)
    for _ in range(10):
        sample(traffic, clock, switch, to_nexus_gbps=8, from_nexus_gbps=2)
    assert not (tmp_path / STATE_FILE).exists()
    for _ in range(30):
        sample(traffic, clock, switch, to_nexus_gbps=8, from_nexus_gbps=2)
    assert (tmp_path / STATE_FILE).stat().st_size < 4096
    reborn = SwitchTraffic(CONFIG, tmp_path, clock, switch.connect)
    assert reborn.snapshot()["links"][0]["buckets"] == link(traffic)["buckets"][:len(reborn.snapshot()["links"][0]["buckets"])]
    assert reborn.snapshot()["links"][0]["peak"]["rx_bps"] == 8_000_000_000


@pytest.mark.parametrize("content", [b"{", b'{"schema":"other","links":{}}', b'{"schema":"storage-viz.network/1","links":{"nexus:bond_nas":[[1,"x"],[61,1,5,5,5,5],[-60,1,1,1,1,1]]}}', b"[]"])
def test_a_damaged_history_file_is_ignored(tmp_path, content):
    (tmp_path / STATE_FILE).write_bytes(content)
    traffic = SwitchTraffic(CONFIG, tmp_path, Clock(), Switch().connect)
    assert all(row[0] % 60 == 0 and row[0] > 0 for row in link(traffic)["buckets"]) and len(link(traffic)["buckets"]) <= 1


def test_a_symlinked_history_file_is_not_followed(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_bytes(b'{"schema":"storage-viz.network/1","links":{"nexus:bond_nas":[[1000000020,1,5,5,5,5]]}}')
    (tmp_path / STATE_FILE).symlink_to(target)
    traffic = SwitchTraffic(CONFIG, tmp_path, Clock(), Switch().connect)
    assert link(traffic)["buckets"] == []


def test_the_password_never_reaches_logs_or_the_snapshot(tmp_path, caplog):
    secret = tmp_path / "secret"
    secret.write_text("hunter2-password\n")
    config = NetworkConfig("192.0.2.1", 8728, "storage-viz", secret, (SPEC,))

    def refuse(*args, **kwargs):
        raise RouterOSError("invalid user name or password (6)")

    traffic = SwitchTraffic(config, None, Clock(), None)
    traffic._connector = lambda: refuse(secret.read_text())
    with caplog.at_level(logging.DEBUG):
        traffic.poll_once()
        traffic.poll_once()
    assert "hunter2-password" not in caplog.text + str(traffic.snapshot())
    assert "RouterOSError" in caplog.text


def env(**values):
    return {f"STORAGE_VIZ_NETWORK_{k}": v for k, v in values.items()}


def test_config_parses_a_valid_setting_and_is_off_when_unset():
    assert parse_config({}) is None
    config = parse_config(env(SWITCH="192.168.0.250:8728", SECRET_FILE="/etc/storage-viz/switch-api.secret",
                              USER="storage-viz", LINKS="nexus:bond_nas:200000, atlas:sfp1:25000"))
    assert (config.host, config.port, config.user) == ("192.168.0.250", 8728, "storage-viz")
    assert [(s.server_id, s.interface, s.capacity_mbps) for s in config.links] == [("nexus", "bond_nas", 200000), ("atlas", "sfp1", 25000)]
    assert parse_config(env(SWITCH="switch.lan", SECRET_FILE="/s", LINKS="a:b:1")).port == 8728


@pytest.mark.parametrize("values,message", [
    (dict(SWITCH="h:8728", LINKS="a:b:1"), "SECRET_FILE is required"),
    (dict(SECRET_FILE="/s", LINKS="a:b:1"), "SWITCH is required"),
    (dict(SWITCH="h:8728", SECRET_FILE="/s"), "LINKS is required"),
    (dict(SWITCH="h:99999", SECRET_FILE="/s", LINKS="a:b:1"), "host or host:port"),
    (dict(SWITCH="h:8728", SECRET_FILE="rel", LINKS="a:b:1"), "absolute"),
    (dict(SWITCH="h:8728", SECRET_FILE="/s", LINKS="a:b"), "server_id:interface:capacity_mbps"),
    (dict(SWITCH="h:8728", SECRET_FILE="/s", LINKS="a/b:c:1"), "server id"),
    (dict(SWITCH="h:8728", SECRET_FILE="/s", LINKS="a:b:0"), "capacity"),
    (dict(SWITCH="h:8728", SECRET_FILE="/s", LINKS="a:b:1,a:b:2"), "twice"),
    (dict(SWITCH="h:8728", SECRET_FILE="/s", USER="bad user", LINKS="a:b:1"), "USER"),
])
def test_config_rejects_garbage_with_a_clear_message(values, message):
    with pytest.raises(ValueError, match=message):
        parse_config(env(**values))
