import pathlib
import tempfile
import threading
import time
import unittest

from collector.inventory import Server
from collector.jobs import RescanJobManager
from collector.store import CentralStore


def make_server(server_id="alpha-1"):
    return Server(
        id=server_id,
        display_name=server_id,
        order=1,
        host=f"{server_id}.example.test",
        port=22,
        enabled=True,
        username="monitoring",
        identity_file=pathlib.PurePosixPath(f"/etc/storage-viz/{server_id}.key"),
        known_hosts_file=pathlib.PurePosixPath("/etc/storage-viz/known_hosts"),
        scanner={"server_id": server_id},
        scanner_digest="a" * 64,
    )


class FakeClock:
    def __init__(self, now=1719200000): self.now = now
    def time(self): return self.now


class BlockingService:
    def __init__(self, servers, store):
        self.servers = tuple(servers)
        self.store = store
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
    def manual_rescan(self, server_id):
        self.calls.append(server_id)
        self.entered.set()
        self.release.wait(2)
        return self.store.load_state(server_id)


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = CentralStore(pathlib.Path(self.tmp.name) / "state")
        self.clock = FakeClock()

    def test_one_active_job_per_server_and_global_concurrency_are_enforced_and_audited(self):
        a = make_server("alpha-1"); b = make_server("beta-2")
        svc = BlockingService([a, b], self.store)
        jobs = RescanJobManager(svc, clock=self.clock, max_concurrent=1)

        first = jobs.request_rescan("alpha-1", "operator-1")
        self.assertEqual(first[0], 202)
        self.assertTrue(svc.entered.wait(2))
        duplicate = jobs.request_rescan("alpha-1", "operator-1")
        self.assertEqual(duplicate[0], 409)
        global_busy = jobs.request_rescan("beta-2", "operator-1")
        self.assertEqual(global_busy[0], 429)
        self.assertEqual([e["result_code"] for e in jobs.audit_events()], ["ACCEPTED", "ACTIVE_JOB", "GLOBAL_CONCURRENCY"])
        self.assertEqual(self.store.load_state("alpha-1")["active_job"]["state"], "running")
        svc.release.set()
        jobs.wait_for_idle(timeout=2)


    def test_restart_reconciles_persisted_running_job_to_failed_and_restores_cooldown(self):
        a = make_server("alpha-1")
        self.store.update_state("alpha-1", active_job={"id":"job-1","server_id":"alpha-1","kind":"rescan","state":"running","actor":"operator-1","requested_unix":self.clock.now - 100,"started_unix":self.clock.now - 99,"finished_unix":None,"result_code":None})
        svc = BlockingService([a], self.store); svc.release.set()
        jobs = RescanJobManager(svc, clock=self.clock, cooldown_seconds=900)
        active = self.store.load_state("alpha-1")["active_job"]
        self.assertEqual(active["state"], "failed")
        self.assertEqual(active["result_code"], "INTERRUPTED")
        cooldown = jobs.request_rescan("alpha-1", "operator-1")
        self.assertEqual(cooldown[0], 429)
        self.assertEqual(cooldown[1]["error"], "COOLDOWN")

    def test_completed_threads_are_removed_from_bounded_bookkeeping(self):
        a = make_server("alpha-1")
        svc = BlockingService([a], self.store); svc.release.set()
        jobs = RescanJobManager(svc, clock=self.clock, cooldown_seconds=0)
        for i in range(3):
            self.clock.now += 1
            self.assertEqual(jobs.request_rescan("alpha-1", "operator-1")[0], 202)
            jobs.wait_for_idle(timeout=2)
            self.assertLessEqual(jobs.active_thread_count(), 0)

    def test_unknown_server_cooldown_and_bounded_audit_fields(self):
        a = make_server("alpha-1")
        svc = BlockingService([a], self.store)
        svc.release.set()
        jobs = RescanJobManager(svc, clock=self.clock, cooldown_seconds=900)

        self.assertEqual(jobs.request_rescan("../bad", "operator-1")[0], 404)
        accepted = jobs.request_rescan("alpha-1", "operator-1")
        self.assertEqual(accepted[0], 202)
        jobs.wait_for_idle(timeout=2)
        self.clock.now += 899
        cooldown = jobs.request_rescan("alpha-1", "bad actor /tmp/secret" + "x" * 200)
        self.assertEqual(cooldown[0], 429)
        self.assertEqual(cooldown[1]["error"], "COOLDOWN")
        event = jobs.audit_events()[-1]
        self.assertLessEqual(len(event["actor"]), 128)
        self.assertNotIn("/", event["actor"])
        self.assertLessEqual(len(event["job_id"]), 128)


if __name__ == "__main__":
    unittest.main()
