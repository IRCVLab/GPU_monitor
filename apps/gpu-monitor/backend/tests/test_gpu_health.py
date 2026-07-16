import unittest

from backend.collectors.gpu_health import GpuInventoryTracker, assess_gpu_inventory


class GpuInventoryHealthTests(unittest.TestCase):
    def test_healthy_inventory_matches_visible_pci_and_history(self) -> None:
        health = assess_gpu_inventory(
            visible_indices=[0, 1],
            pci_count=2,
            historical_indices={0, 1},
        )

        self.assertEqual(health.visible_count, 2)
        self.assertEqual(health.expected_count, 2)
        self.assertEqual(health.pci_count, 2)
        self.assertEqual(health.missing_indices, [])
        self.assertEqual(health.state, "healthy")

    def test_pci_count_greater_than_visible_is_suspect_without_invented_missing_indices(self) -> None:
        health = assess_gpu_inventory(
            visible_indices=[0, 1],
            pci_count=3,
            historical_indices=set(),
        )

        self.assertEqual(health.visible_count, 2)
        self.assertEqual(health.expected_count, 3)
        self.assertEqual(health.pci_count, 3)
        self.assertEqual(health.missing_indices, [])
        self.assertEqual(health.state, "suspect")

    def test_learned_indices_missing_are_reported(self) -> None:
        health = assess_gpu_inventory(
            visible_indices=[0, 1],
            pci_count=2,
            historical_indices={0, 1, 2},
        )

        self.assertEqual(health.visible_count, 2)
        self.assertEqual(health.expected_count, 3)
        self.assertEqual(health.missing_indices, [2])
        self.assertEqual(health.state, "suspect")

    def test_one_sample_suspect_does_not_debounce_to_missing(self) -> None:
        tracker = GpuInventoryTracker(historical_indices={0, 1, 2})

        first = tracker.assess(visible_indices=[0, 1], pci_count=3)

        self.assertEqual(first.missing_indices, [2])
        self.assertEqual(first.state, "suspect")

    def test_two_consecutive_mismatches_mark_missing(self) -> None:
        tracker = GpuInventoryTracker(historical_indices={0, 1, 2})

        tracker.assess(visible_indices=[0, 1], pci_count=3)
        second = tracker.assess(visible_indices=[0, 1], pci_count=3)

        self.assertEqual(second.visible_count, 2)
        self.assertEqual(second.expected_count, 3)
        self.assertEqual(second.missing_indices, [2])
        self.assertEqual(second.state, "missing")

    def test_recovery_resets_mismatch_debounce(self) -> None:
        tracker = GpuInventoryTracker(historical_indices={0, 1, 2})

        tracker.assess(visible_indices=[0, 1], pci_count=3)
        recovered = tracker.assess(visible_indices=[0, 1, 2], pci_count=3)
        suspect_again = tracker.assess(visible_indices=[0, 1], pci_count=3)

        self.assertEqual(recovered.state, "healthy")
        self.assertEqual(suspect_again.state, "suspect")

    def test_current_visible_indices_expand_expected_inventory(self) -> None:
        tracker = GpuInventoryTracker(historical_indices={0})

        healthy = tracker.assess(visible_indices=[0, 2], pci_count=2)
        missing = tracker.assess(visible_indices=[0], pci_count=2)

        self.assertEqual(healthy.expected_count, 2)
        self.assertEqual(healthy.state, "healthy")
        self.assertEqual(missing.missing_indices, [2])
        self.assertEqual(missing.state, "suspect")


from datetime import datetime

from backend.collectors.gpu import GpuInfo, ServerGpuData
from backend.collectors.server_collector import ServerCollector
from backend.models import Server


class ServerCollectorGpuInventoryTests(unittest.IsolatedAsyncioTestCase):
    def _collector(self) -> ServerCollector:
        server = Server(id=7, name="test-server", host="127.0.0.1", port=22, ssh_user="ircv")
        collector = ServerCollector(server)
        collector._sync_collect_storage = lambda: None
        collector._load_historical_gpu_indices_once = self._load_historical_once(collector, {0, 1, 2})
        return collector

    def _load_historical_once(self, collector: ServerCollector, indices: set[int]):
        calls = {"count": 0}

        async def load() -> set[int]:
            calls["count"] += 1
            collector._gpu_inventory_tracker.add_historical_indices(indices)
            collector._historical_gpu_indices_loaded = True
            collector._test_history_load_calls = calls["count"]
            return collector._gpu_inventory_tracker.expected_indices

        return load

    def _gpu_data(self, indices: list[int]) -> ServerGpuData:
        return ServerGpuData(
            gpus=[
                GpuInfo(
                    index=index,
                    name=f"GPU {index}",
                    utilization=0,
                    memory_used=0,
                    memory_total=1024,
                    temperature=30,
                    power_draw=10,
                    users=[],
                )
                for index in indices
            ],
            collected_at=datetime(2026, 1, 1),
        )

    async def test_collector_keeps_one_sample_inventory_suspect_online(self) -> None:
        collector = self._collector()
        collector._sync_collect_gpu = lambda: self._gpu_data([0, 1])
        collector._sync_collect_system = lambda: "1.0,1048576,2097152,0.0,0.0,0,1000,2000,10.0,3,7.5,6,0.8,1.2,1.5,64"

        data, degraded, reason = await collector._collect_once()

        self.assertFalse(degraded)
        self.assertIsNone(reason)
        self.assertEqual(data["gpu_inventory"]["state"], "suspect")
        self.assertEqual(data["gpu_inventory"]["missing_indices"], [2])
        self.assertEqual(data["system"]["cpu_pressure_some"], 7.5)
        self.assertEqual(data["system"]["cpu_running_tasks"], 6)
        self.assertEqual(data["system"]["load_avg_1"], 0.8)
        self.assertEqual(data["system"]["load_avg_5"], 1.2)
        self.assertEqual(data["system"]["load_avg_15"], 1.5)
        self.assertEqual(data["system"]["cpu_count"], 64)
        self.assertEqual(collector._test_history_load_calls, 1)

    async def test_collector_marks_two_inventory_mismatches_degraded_and_adds_disk_rate(self) -> None:
        collector = self._collector()
        collector._sync_collect_gpu = lambda: self._gpu_data([0, 1])
        samples = iter([
            "1.0,1048576,2097152,0.0,0.0,0,1000,2000,10.0,3,7.5,6,0.8,1.2,1.5,64",
            "1.0,1048576,2097152,0.0,0.0,0,3000,5000,12.0,3,8.5,7,1.8,2.2,2.5,64",
        ])
        collector._sync_collect_system = lambda: next(samples)

        await collector._collect_once()
        data, degraded, reason = await collector._collect_once()

        self.assertTrue(degraded)
        self.assertEqual(reason["code"], "gpu_device_missing")
        self.assertEqual(data["gpu_inventory"]["state"], "missing")
        self.assertEqual(data["gpu_inventory"]["visible_count"], 2)
        self.assertEqual(data["gpu_inventory"]["expected_count"], 3)
        self.assertEqual(data["gpu_inventory"]["missing_indices"], [2])
        self.assertEqual(data["system"]["cpu_pressure_some"], 8.5)
        self.assertEqual(data["system"]["cpu_running_tasks"], 7)
        self.assertEqual(data["system"]["load_avg_1"], 1.8)
        self.assertEqual(data["system"]["load_avg_5"], 2.2)
        self.assertEqual(data["system"]["load_avg_15"], 2.5)
        self.assertEqual(data["system"]["cpu_count"], 64)
        self.assertEqual(data["system"]["disk_read_bytes_per_second"], 1000.0)
        self.assertEqual(data["system"]["disk_write_bytes_per_second"], 1500.0)
        self.assertEqual(data["system"]["disk_sample_seconds"], 2.0)

    async def test_collector_retains_gpu_collect_failed_when_gpu_collection_fails(self) -> None:
        collector = self._collector()
        collector._ssh = type("FakeSSH", (), {"is_connected": True})()
        collector._sync_collect_gpu = lambda: (_ for _ in ()).throw(RuntimeError("nvidia-smi failed"))
        collector._sync_collect_system = lambda: "1.0,1048576,2097152,0.0,0.0,0,1000,2000,10.0,3"

        data, degraded, reason = await collector._collect_once()

        self.assertTrue(degraded)
        self.assertEqual(reason["code"], "gpu_collect_failed")
        self.assertIsNone(data["gpu_inventory"])
        self.assertEqual(data["gpus"], [])


if __name__ == "__main__":
    unittest.main()
