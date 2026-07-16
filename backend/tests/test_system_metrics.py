import unittest

from backend.collectors import system as system_module
from backend.collectors.system import SYSTEM_CMD_PROC, parse_system


class SystemMetricsTests(unittest.TestCase):
    def test_system_proc_command_reads_psi_blocked_diskstats_and_pci_inventory(self) -> None:
        self.assertIn('/proc/pressure/io', SYSTEM_CMD_PROC)
        self.assertIn('procs_blocked', SYSTEM_CMD_PROC)
        self.assertIn('/proc/diskstats', SYSTEM_CMD_PROC)
        self.assertIn('/sys/block', SYSTEM_CMD_PROC)
        self.assertIn('islink', SYSTEM_CMD_PROC)
        self.assertIn('/sys/bus/pci/devices', SYSTEM_CMD_PROC)
        self.assertIn('0x10de', SYSTEM_CMD_PROC)

    def test_system_proc_command_reads_cpu_some_running_tasks_and_loadavg_without_cpu_full(self) -> None:
        self.assertIn('/proc/pressure/cpu', SYSTEM_CMD_PROC)
        self.assertIn('procs_running', SYSTEM_CMD_PROC)
        self.assertIn('os.getloadavg', SYSTEM_CMD_PROC)
        self.assertIn('os.cpu_count', SYSTEM_CMD_PROC)
        self.assertNotIn('cpu_pressure_full', SYSTEM_CMD_PROC)

    def test_parse_system_reads_extended_psi_and_blocked_tasks(self) -> None:
        info = parse_system("12.5,1073741824,2147483648,0.33,0.11,7")

        self.assertEqual(info.cpu_percent, 12.5)
        self.assertEqual(info.ram_used, 1024)
        self.assertEqual(info.ram_total, 2048)
        self.assertEqual(info.io_pressure_some, 0.33)
        self.assertEqual(info.io_pressure_full, 0.11)
        self.assertEqual(info.io_blocked_tasks, 7)
        self.assertTrue(info.io_pressure_supported)


    def test_parse_system_reads_disk_counters_sample_time_and_pci_gpu_count(self) -> None:
        info = parse_system("12.5,1073741824,2147483648,0.33,0.11,7,4096,8192,123.25,2")

        self.assertEqual(info.cpu_percent, 12.5)
        self.assertEqual(info.ram_used, 1024)
        self.assertEqual(info.ram_total, 2048)
        self.assertEqual(info.io_pressure_some, 0.33)
        self.assertEqual(info.io_pressure_full, 0.11)
        self.assertEqual(info.io_blocked_tasks, 7)
        self.assertTrue(info.io_pressure_supported)
        self.assertEqual(info.disk_read_bytes_total, 4096)
        self.assertEqual(info.disk_write_bytes_total, 8192)
        self.assertEqual(info.disk_sample_time, 123.25)
        self.assertEqual(info.pci_gpu_count, 2)

    def test_parse_system_reads_cpu_pressure_and_running_tasks(self) -> None:
        info = parse_system("12.0,1048576,2097152,1.0,0.2,2,100,200,3.0,4,7.5,6")

        self.assertEqual(info.cpu_pressure_some, 7.5)
        self.assertEqual(info.cpu_running_tasks, 6)
        self.assertIsNone(info.load_avg_1)
        self.assertIsNone(info.load_avg_5)
        self.assertIsNone(info.load_avg_15)
        self.assertIsNone(info.cpu_count)

    def test_parse_system_reads_load_average_and_cpu_count(self) -> None:
        info = parse_system("12.0,1048576,2097152,1.0,0.2,2,100,200,3.0,4,7.5,6,0.8,1.2,1.5,64")

        self.assertEqual(info.cpu_pressure_some, 7.5)
        self.assertEqual(info.cpu_running_tasks, 6)
        self.assertEqual(info.load_avg_1, 0.8)
        self.assertEqual(info.load_avg_5, 1.2)
        self.assertEqual(info.load_avg_15, 1.5)
        self.assertEqual(info.cpu_count, 64)

    def test_parse_system_preserves_six_field_output_without_disk_or_pci_fields(self) -> None:
        info = parse_system("12.5,1073741824,2147483648,0.33,0.11,7")

        self.assertEqual(info.cpu_percent, 12.5)
        self.assertEqual(info.ram_used, 1024)
        self.assertEqual(info.ram_total, 2048)
        self.assertEqual(info.io_pressure_some, 0.33)
        self.assertEqual(info.io_pressure_full, 0.11)
        self.assertEqual(info.io_blocked_tasks, 7)
        self.assertTrue(info.io_pressure_supported)
        self.assertIsNone(info.disk_read_bytes_total)
        self.assertIsNone(info.disk_write_bytes_total)
        self.assertIsNone(info.disk_sample_time)
        self.assertIsNone(info.pci_gpu_count)

    def test_calculate_disk_io_rate_uses_elapsed_sample_time(self) -> None:
        previous = parse_system("1.0,1048576,2097152,0.0,0.0,0,1024,2048,10.0,1")
        current = parse_system("1.0,1048576,2097152,0.0,0.0,0,3072,6144,12.0,1")

        rate = system_module.calculate_disk_io_rate(previous, current)

        self.assertIsNotNone(rate)
        self.assertEqual(rate.read_bytes_per_second, 1024.0)
        self.assertEqual(rate.write_bytes_per_second, 2048.0)

    def test_calculate_disk_io_rate_rejects_counter_rollback(self) -> None:
        previous = parse_system("1.0,1048576,2097152,0.0,0.0,0,4096,8192,10.0,1")
        current = parse_system("1.0,1048576,2097152,0.0,0.0,0,3072,9000,12.0,1")

        self.assertIsNone(system_module.calculate_disk_io_rate(previous, current))

    def test_calculate_disk_io_rate_rejects_missing_or_non_positive_elapsed_samples(self) -> None:
        previous = parse_system("1.0,1048576,2097152,0.0,0.0,0,1024,2048,10.0,1")
        same_time = parse_system("1.0,1048576,2097152,0.0,0.0,0,2048,4096,10.0,1")
        missing_disk = parse_system("1.0,1048576,2097152")

        self.assertIsNone(system_module.calculate_disk_io_rate(previous, same_time))
        self.assertIsNone(system_module.calculate_disk_io_rate(previous, missing_disk))

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
