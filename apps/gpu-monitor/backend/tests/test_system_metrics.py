import unittest

from backend.collectors.system import SYSTEM_CMD_PROC, parse_system


class SystemMetricsTests(unittest.TestCase):
    def test_system_proc_command_reads_psi_and_blocked_tasks(self) -> None:
        self.assertIn('/proc/pressure/io', SYSTEM_CMD_PROC)
        self.assertIn('procs_blocked', SYSTEM_CMD_PROC)

    def test_parse_system_reads_extended_psi_and_blocked_tasks(self) -> None:
        info = parse_system("12.5,1073741824,2147483648,0.33,0.11,7")

        self.assertEqual(info.cpu_percent, 12.5)
        self.assertEqual(info.ram_used, 1024)
        self.assertEqual(info.ram_total, 2048)
        self.assertEqual(info.io_pressure_some, 0.33)
        self.assertEqual(info.io_pressure_full, 0.11)
        self.assertEqual(info.io_blocked_tasks, 7)
        self.assertTrue(info.io_pressure_supported)

    def test_parse_system_preserves_legacy_three_field_output(self) -> None:
        info = parse_system("7.0,1073741824,2147483648")

        self.assertEqual(info.cpu_percent, 7.0)
        self.assertEqual(info.ram_used, 1024)
        self.assertEqual(info.ram_total, 2048)
        self.assertIsNone(info.io_pressure_some)
        self.assertIsNone(info.io_pressure_full)
        self.assertIsNone(info.io_blocked_tasks)
        self.assertFalse(info.io_pressure_supported)

    def test_parse_system_tolerates_empty_malformed_and_unsupported_psi_fields(self) -> None:
        cases = (
            ("12.5,1073741824,2147483648,,,7", 7),
            ("12.5,1073741824,2147483648,unsupported,unsupported,7", 7),
            ("12.5,1073741824,2147483648,0.33,not-a-number,7", 7),
        )

        for raw, blocked_tasks in cases:
            with self.subTest(raw=raw):
                info = parse_system(raw)
                self.assertIsNone(info.io_pressure_some)
                self.assertIsNone(info.io_pressure_full)
                self.assertEqual(info.io_blocked_tasks, blocked_tasks)
                self.assertFalse(info.io_pressure_supported)


if __name__ == "__main__":
    unittest.main()
