import http.client
import json
import threading

import pytest
from collector.test_bug_harness import Collector


@pytest.fixture
def collector_api(tmp_path, monkeypatch):
    from api import server as serve

    host = Collector(tmp_path, "alpha-1", "beta-2")
    origin = "https://dashboard.example.test"
    runtime = serve.Runtime(runs_actors=True, rescan_enabled=True)
    for name, value in {
        "TRUSTED_PROXY": True,
        "DIRECT_LOOPBACK_RESCAN": False, "OPERATORS": {"operator"}, "ALLOWED_ORIGINS": {origin},
    }.items():
        monkeypatch.setattr(serve.auth, name, value)

    def use_fleet():
        runtime.fleet = host.fleet

    use_fleet()
    host.use_fleet = use_fleet
    with serve.Server(("127.0.0.1", 0), runtime) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()

        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=6)
            try:
                conn.request(method, path, body, {serve.auth.IDENTITY_HEADER: "operator", **(headers or {})})
                with conn.getresponse() as response:
                    return response.status, json.load(response), response.headers
            finally:
                conn.close()

        try:
            yield host, request, origin
        finally:
            server.shutdown()
            thread.join()
            host.fleet.stop()
