import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.collectors import experiment_probe as probe


class ExperimentProbeTests(unittest.TestCase):
    def setUp(self):
        self.namespace = {"__name__": "probe_fixture"}
        exec(probe._PROBE_SOURCE, self.namespace)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.cwd = self.root / "work"
        self.cwd.mkdir()

    def make_process(self, pid, start, *, pgid=None, entrypoint="train.py", log=None, args=(), log_flags=1):
        process = self.proc / str(pid)
        process.mkdir()
        stat = ["S", "1", str(pgid or pid), "1"] + ["0"] * 15 + [str(start)]
        (process / "stat").write_text(f"{pid} (task name) " + " ".join(stat))
        argv = ["/usr/bin/python3", "-u", entrypoint, *args, "--token", "private-value"]
        (process / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
        (process / "cwd").symlink_to(self.cwd)
        (process / "exe").symlink_to("/usr/bin/python3")
        (process / "fd").mkdir()
        (process / "fdinfo").mkdir()
        if log is not None:
            (process / "fd" / "1").symlink_to(log)
            (process / "fdinfo" / "1").write_text(f"flags:\t{log_flags:o}\nino:\t{log.stat().st_ino}\n")
        return process

    def snapshot(self, jobs, *, boot="boot-a"):
        return self.namespace["snapshot"](self.proc, boot, jobs)

    def test_remote_evidence_contains_only_entrypoint_and_numbers(self):
        log = self.cwd / "training.log"
        log.write_text("private-value /confidential/path\noverall 42%| [00:04<12:34, 2it/s]\n")
        self.make_process(101, 1024, entrypoint="tools/train.py", log=log, args=("--config", "/secret/path/vad.yaml"))
        row, = self.snapshot({101: {0, 3}})
        self.assertEqual(row["entrypoint"], "train.py")
        self.assertEqual(row["config_name"], "vad.yaml")
        self.assertEqual(row["gpu_indices"], [0, 3])
        self.assertIn(row["eta_seconds"], (753, 754))
        self.assertNotIn("private-value", json.dumps(row))
        self.assertNotIn("/secret/path", json.dumps(row))
        self.assertNotIn("training.log", json.dumps(row))

    def test_only_recognized_arguments_can_supply_a_config_basename(self):
        self.make_process(101, 12, args=("--token", "private.json"))
        self.make_process(102, 13, args=("configs/transfuser.py",))
        self.make_process(103, 14, args=("--config-file=configs/drive.toml",))
        self.make_process(104, 15, entrypoint="report.py", args=("private.yaml",))
        rows = self.snapshot({101: {0}, 102: {1}, 103: {2}, 104: {3}})
        self.assertEqual(
            [row["config_name"] for row in rows], [None, "transfuser.py", "drive.toml", None],
        )

    def test_reads_actual_file_handler_fd_when_stdio_has_no_progress(self):
        output = self.cwd / "stdout.log"
        output.write_text("training started")
        process = self.make_process(101, 24, log=output)
        runs = self.cwd / "work_dirs"
        runs.mkdir()
        handler = logging.FileHandler(runs / "training.log")
        self.addCleanup(handler.close)
        handler.stream.write(
            "11/24 22:00:18 - mmengine - INFO - Epoch(train) [3][100/1000] "
            "lr: 0.0001 eta: 7:21:11 time: 0.83 loss: 0.09\n"
        )
        handler.flush()
        (process / "fd" / "9").symlink_to(f"/proc/self/fd/{handler.stream.fileno()}")
        (process / "fdinfo" / "9").symlink_to(f"/proc/self/fdinfo/{handler.stream.fileno()}")
        row, = self.snapshot({101: {0}})
        self.assertIn(row["eta_seconds"], (26470, 26471))

    def test_ignores_already_open_read_only_log_like_files(self):
        log = self.cwd / "training.log"
        log.write_text("eta: 00:10:00")
        self.make_process(101, 24, log=log, log_flags=os.O_RDONLY)
        row, = self.snapshot({101: {0}})
        self.assertIsNone(row["eta_seconds"])

    def test_unknown_or_unrelated_progress_does_not_set_eta(self):
        eta = self.namespace["parse_eta"]
        self.assertEqual(eta("Train epoch 4 [1/20], eta: 1:02:03"), 3723)
        self.assertEqual(eta("Run ETA: 01:02:03"), 3723)
        self.assertIsNone(eta("42%| [00:04<12:34, 2it/s]"))
        self.assertIsNone(eta("epoch ETA: 0:40:50"))
        self.assertIsNone(eta("validation accuracy 91.5%"))
        self.assertIsNone(eta("eta: 999:02:03"))
        self.assertIsNone(eta("eta: 0:90:03"))

    def test_skips_stale_logs_and_paths_outside_the_working_directory(self):
        outside = self.root / "other.log"
        inside = self.cwd / "training.log"
        for path in (outside, inside):
            path.write_text("eta: 00:10:00")
        os.utime(inside, (1, 1))
        self.make_process(102, 10, log=outside)
        self.make_process(103, 11, log=inside)
        rows = self.snapshot({102: {0}, 103: {1}})
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["eta_seconds"] is None for row in rows))

    def test_eta_counts_down_from_the_log_write(self):
        timestamp = 1700000000
        log = self.cwd / "training.log"
        log.write_text("eta: 00:05:00")
        os.utime(log, (timestamp, timestamp))
        self.make_process(102, 10, log=log)
        clock = self.namespace["time"]
        for age, expected in ((0, 300), (240, 60), (300, None)):
            with patch.object(clock, "time", return_value=timestamp + age):
                row, = self.snapshot({102: {0}})
            self.assertEqual(row["eta_seconds"], expected)

    def test_only_ranks_with_common_process_group_and_log_share_a_note(self):
        log = self.cwd / "training.log"
        log.write_text("eta: 00:10:00")
        self.make_process(100, 10, log=log)
        self.make_process(101, 12, pgid=100, log=log)
        self.make_process(102, 14, pgid=100, log=log)
        self.make_process(103, 16, log=log)
        rows = self.snapshot({101: {0}, 102: {1}, 103: {2}})
        self.assertEqual({tuple(row["gpu_indices"]) for row in rows}, {(0, 1), (2,)})
        combined = next(row for row in rows if row["gpu_indices"] == [0, 1])
        survivor, = self.snapshot({102: {1}})
        self.assertEqual(combined["id"], survivor["id"])

    def test_process_identity_changes_on_reuse_or_reboot_and_skips_gone_pid(self):
        process = self.make_process(101, 12)
        first, = self.snapshot({101: {1}, 105: {0}})
        reboot, = self.snapshot({101: {1}}, boot="boot-b")
        self.assertNotEqual(first["id"], reboot["id"])
        stat_file = process / "stat"
        stat_file.write_text(stat_file.read_text().rsplit(" ", 1)[0] + " 99")
        reused, = self.snapshot({101: {1}})
        self.assertNotEqual(first["id"], reused["id"])

    def test_collect_experiments_closes_connection_and_rejects_extra_data(self):
        server = SimpleNamespace(name="fixture")
        with patch.object(probe, "SSHClient") as ssh_client:
            ssh = ssh_client.return_value
            valid = {
                "id": "a" * 32, "entrypoint": "train.py", "gpu_indices": [0, 2],
                "eta_seconds": 900, "config_name": None,
            }
            ssh.run_bounded.return_value = json.dumps([valid])
            self.assertEqual(probe.collect_experiments(server), [
                probe.ExperimentEvidence("a" * 32, "train.py", (0, 2), 900),
            ])
            ssh.close.assert_called_once()
            ssh.run_bounded.return_value = json.dumps([{**valid, "entrypoint": "private/train.py"}])
            with self.assertRaisesRegex(ValueError, "Invalid experiment evidence"):
                probe.collect_experiments(server)
            self.assertEqual(ssh.close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
