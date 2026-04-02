from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from backend.collectors.manager import resolve_collector_export_state


class ManagerFreshnessTests(unittest.TestCase):
    def test_recent_online_state_stays_online(self) -> None:
        now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
        collector = SimpleNamespace(
            status="online",
            last_seen=now - timedelta(seconds=20),
            offline_since=None,
            status_reason=None,
        )

        status, offline_since, reason = resolve_collector_export_state(
            collector,
            now=now,
            stale_warn_seconds=60,
            stale_offline_seconds=600,
        )

        self.assertEqual(status, "online")
        self.assertIsNone(offline_since)
        self.assertIsNone(reason)

    def test_stale_online_state_becomes_degraded(self) -> None:
        now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
        collector = SimpleNamespace(
            status="online",
            last_seen=now - timedelta(seconds=75),
            offline_since=None,
            status_reason=None,
        )

        status, offline_since, reason = resolve_collector_export_state(
            collector,
            now=now,
            stale_warn_seconds=60,
            stale_offline_seconds=600,
        )

        self.assertEqual(status, "degraded")
        self.assertIsNone(offline_since)
        self.assertEqual(reason["code"], "stale_snapshot")

    def test_very_stale_online_state_becomes_offline(self) -> None:
        now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
        collector = SimpleNamespace(
            status="online",
            last_seen=now - timedelta(seconds=900),
            offline_since=None,
            status_reason=None,
        )

        status, offline_since, reason = resolve_collector_export_state(
            collector,
            now=now,
            stale_warn_seconds=60,
            stale_offline_seconds=600,
        )

        self.assertEqual(status, "offline")
        self.assertEqual(offline_since, collector.last_seen)
        self.assertEqual(reason["code"], "stale_offline")


if __name__ == "__main__":
    unittest.main()
