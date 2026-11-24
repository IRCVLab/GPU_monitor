import json
import tempfile
import unittest
from pathlib import Path

from backend.hardware import parse_sensor_snapshot, read_hardware_snapshots


class HardwareSnapshotTests(unittest.TestCase):
    def test_preserves_unavailable_values_and_sensor_thresholds(self):
        raw = ('CPU1 Temperature | 37.000 | degrees C | ok | 0 | 0 | 0 | 93 | 96 | 99\n'
               'CPU2 Temperature | na | degrees C | na | na | na | na | 93 | 96 | 99\n'
               'PSU1 Power In | 336.000 | Watts | ok | na | na | na | 2352 | 2480 | 2608\n'
               'FRNT_FAN1 | 5880.000 | RPM | ok | 0 | 720 | 720 | na | na | na\n'
               'Instantaneous power reading: 353 Watts\n'
               'Maximum during sampling period: 17860 Watts\n')
        result = parse_sensor_snapshot(raw, '2026-11-12T08:00:00+00:00', '192.168.0.215')
        self.assertEqual(result['power_watts'], 353)
        self.assertEqual(result['sensors'][0]['upper_warning'], 93)
        self.assertIsNone(result['sensors'][1]['value'])
        self.assertEqual(result['sensors'][3]['kind'], 'fan')
        self.assertNotIn('17860', json.dumps(result))

    def test_ignores_discrete_and_invalid_numeric_readings(self):
        raw = ('PSU AC Lost | 0x0 | discrete | 0x0180 | na | na | na | na | na | na\n'
               'Bad | nan | Volts | ok | na | na | na | na | na | na\n'
               'Instantaneous power reading: -1 Watts\n')
        result = parse_sensor_snapshot(raw, '2026-11-12T08:00:00+00:00', None)
        self.assertIsNone(result['power_watts'])
        self.assertIsNone(result['sensors'][0]['value'])
        self.assertEqual(result['sensors'][0]['kind'], 'voltage')

    def test_missing_or_invalid_snapshot_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'hardware.json'
            self.assertEqual(read_hardware_snapshots(path), {})
            path.write_text('{broken')
            self.assertEqual(read_hardware_snapshots(path), {})

    def test_reader_does_not_expose_unknown_fields_or_raw_command_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'hardware.json'
            snapshot = parse_sensor_snapshot('', '2026-11-12T08:00:00+00:00', None)
            snapshot['ssh_password'] = 'must-not-be-returned'
            path.write_text(json.dumps({'2': snapshot}))
            result = read_hardware_snapshots(path)
            self.assertEqual(result['2']['source'], 'ssh-ipmi-snapshot')
            self.assertNotIn('ssh_password', result['2'])
