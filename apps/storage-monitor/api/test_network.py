import http.client
import json
from pathlib import Path
import threading

import pytest

from api import server as serve
from collector.switch_traffic import LinkSpec, NetworkConfig, SwitchTraffic

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fleet"


class Clock:
    unix = 1_000_000_020.0

    def time(self):
        return self.unix

    def monotonic(self):
        return self.unix - 1_000_000_000


def get(runtime, path="/api/v2/network", headers=None):
    with serve.Server(("127.0.0.1", 0), runtime) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            conn = http.client.HTTPConnection(*server.server_address, timeout=5)
            conn.request("GET", path, headers=headers or {})
            response = conn.getresponse()
            return response.status, json.load(response)
        finally:
            server.shutdown()
            thread.join()


def test_network_is_empty_when_the_feature_is_off():
    runtime = serve.configure(FIXTURES)
    runtime.network = None
    assert get(runtime) == (200, {"links": []})


def test_network_reports_each_link_with_now_peak_and_buckets():
    clock, counters = Clock(), {"rx": 0, "tx": 0}

    class Client:
        def talk(self, words):
            return [{"name": "bond_nas", "rx-byte": str(counters["rx"]), "tx-byte": str(counters["tx"])}]

    config = NetworkConfig("192.0.2.1", 8728, "u", Path("/s"), (LinkSpec("nexus", "bond_nas", 200_000),))
    traffic = SwitchTraffic(config, None, clock, Client)
    traffic.poll_once()
    clock.unix += 2
    counters["tx"], counters["rx"] = 250_000_000, 125_000_000
    traffic.poll_once()
    status, body = get(serve.Runtime(fleet=serve.FixtureFleet(FIXTURES), network=traffic))
    assert status == 200 and body["sample_seconds"] == 2 and body["generated_unix"] == 1_000_000_022
    (link,) = body["links"]
    assert set(link) == {"server_id", "interface", "capacity_bps", "now", "peak", "buckets", "state"}
    assert (link["server_id"], link["interface"], link["capacity_bps"], link["state"]) == ("nexus", "bond_nas", 200_000_000_000, "ok")
    assert link["now"] == {"rx_bps": 1_000_000_000, "tx_bps": 500_000_000, "unix": 1_000_000_022}
    assert link["peak"] == {"rx_bps": 1_000_000_000, "rx_unix": 1_000_000_020, "tx_bps": 500_000_000, "tx_unix": 1_000_000_020}
    assert link["buckets"] == [[1_000_000_020, 1_000_000_000, 1_000_000_000, 500_000_000, 500_000_000]]


def test_sample_mode_serves_a_full_day_that_ends_now():
    status, body = get(serve.configure(FIXTURES))
    (link,) = body["links"]
    assert status == 200 and link["server_id"] == "nexus" and link["state"] == "ok"
    assert 1400 < len(link["buckets"]) <= 1441
    assert 15e9 < link["peak"]["rx_bps"] < 27e9 and all(row[2] >= row[1] for row in link["buckets"])


def test_network_follows_read_auth(monkeypatch):
    monkeypatch.setattr(serve.auth, "TRUSTED_PROXY", True)
    runtime = serve.configure(FIXTURES)
    assert get(runtime)[0] == 401
    assert get(runtime, headers={serve.auth.IDENTITY_HEADER: "someone"})[0] == 200
    assert get(runtime, "/api/v2/network?x=1", {serve.auth.IDENTITY_HEADER: "someone"})[0] == 400


def test_bad_network_config_stops_the_dashboard_at_startup(monkeypatch, tmp_path):
    inventory = tmp_path / "servers.json"
    inventory.write_text("{}")
    monkeypatch.setattr(serve.auth, "INVENTORY_PATH", inventory)
    monkeypatch.setenv("STORAGE_VIZ_NETWORK_LINKS", "nexus:bond_nas:200000")
    with pytest.raises(SystemExit, match="STORAGE_VIZ_NETWORK_SWITCH is required"):
        serve.configure()
