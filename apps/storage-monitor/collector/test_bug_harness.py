"""Collector bug fixtures with a real on-disk store and a simulated remote host."""
from collector.scan_telemetry import empty_status
from collector.service import PollService
from collector.store import CentralStore
from collector.test_service import FakeClock, FakeTransport, make_server, payload_for, status_data
from collector.transport import RemoteScanObservation


class PublishingTransport(FakeTransport):
    def publish(self, server_id, started=1719200000):
        status, data = status_data(payload_for(server_id, started=started))
        self.status[server_id] = status
        self.downloads[server_id] = status, data

    def fetch_scan_status(self, server):
        publication = self.status[server.id]
        generation = publication["generation"].removesuffix(".json")
        status = empty_status()
        status.update(state="complete", generation=generation, published_generation=generation,
                      published_finished_unix=publication["scan_finished_unix"])
        return RemoteScanObservation(status, publication.copy())


class Collector:
    def __init__(self, tmp_path, *ids):
        self.state_dir = tmp_path / "state"
        self.transport = PublishingTransport()
        self.clock = FakeClock()
        self.servers = [make_server(sid, order=i) for i, sid in enumerate(ids, 1)]
        self.restart()
        for sid in ids:
            self.transport.publish(sid)
        states = self.service.poll_once()
        assert all(states[sid]["latest_pull_status"] == "succeeded" for sid in ids)

    def restart(self):
        self.store = CentralStore(self.state_dir)
        self.service = PollService(self.servers, self.store, self.transport, clock=self.clock)

    def tick(self):
        self.clock.now += 61
        self.service.poll_scan_status_once()
        # Observation workers can start refresh workers, so join them in that order.
        for group in (self.service._telemetry_threads, self.service._refresh_threads):
            for worker in list(group.values()):
                worker.join(5)
                assert not worker.is_alive(), "telemetry tick did not finish"
