import hashlib
import json
import os
import pathlib
import signal
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest

from collector.inventory import Server
from collector.test_snapshot import base_payload, status_for


class Completed:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.statuses = []
        self.snapshots = {}
        self.fail_next = None
        self.returncode_next = None
        self.truncate_to_cap_on_nonzero = False
        self.seen_temp_modes = []
        self.seen_temp_is_regular = []

    def run(self, argv, *, input=None, stdout=None, stderr=None, timeout=None, check=False, shell=False, preexec_fn=None):
        self.calls.append({"argv": list(argv), "input": input, "timeout": timeout, "shell": shell, "preexec_fn": preexec_fn})
        if shell is not False:
            raise AssertionError("transport must never use shell=True")
        if self.fail_next:
            exc = self.fail_next
            self.fail_next = None
            raise exc
        if argv[0] == "ssh":
            return Completed(stdout=b"", stderr=b"ignored /secret/path")
        if argv[0] != "sftp":
            raise AssertionError(argv)
        batch = input.decode("utf-8") if isinstance(input, bytes) else input
        lines = [line for line in batch.splitlines() if line]
        if len(lines) != 1:
            raise AssertionError(f"unexpected batch: {batch!r}")
        parts = shlex.split(lines[0], posix=True)
        if parts[0] != "get" or len(parts) != 3:
            raise AssertionError(f"unexpected batch: {batch!r}")
        remote, local = parts[1], parts[2]
        st = os.lstat(local)
        self.seen_temp_modes.append(stat.S_IMODE(st.st_mode))
        self.seen_temp_is_regular.append(stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode))
        if remote == "/var/lib/storage-viz/scan-status.json":
            content = json.dumps(self.statuses.pop(0)).encode("utf-8")
            code = self.returncode_next if self.returncode_next is not None else 0
            limit = getattr(preexec_fn, "limit_bytes", len(content))
            pathlib.Path(local).write_bytes(content[:limit] if code and self.truncate_to_cap_on_nonzero else content)
            self.returncode_next = None
            return Completed(returncode=code, stdout=b"", stderr=b"ignored")
        content = self.snapshots[pathlib.PurePosixPath(remote).name]
        code = self.returncode_next if self.returncode_next is not None else 0
        limit = getattr(preexec_fn, "limit_bytes", len(content))
        pathlib.Path(local).write_bytes(content[:limit] if code and self.truncate_to_cap_on_nonzero else content)
        self.returncode_next = None
        return Completed(returncode=code, stdout=b"", stderr=b"ignored")


def server():
    return Server(
        id="alpha-1",
        display_name="Alpha",
        order=2,
        host="alpha.example.test",
        port=2222,
        enabled=True,
        username="monitoring",
        identity_file=pathlib.PurePosixPath("/etc/storage-viz/alpha.key"),
        known_hosts_file=pathlib.PurePosixPath("/etc/storage-viz/known_hosts"),
        scanner={"server_id": "alpha-1"},
        scanner_digest="a" * 64,
    )


class OpenSshTransportTests(unittest.TestCase):
    def test_fetch_snapshot_uses_fixed_sftp_argv_batch_temp_mode_and_race_check(self):
        from collector.transport import OpenSshTransport

        payload = base_payload()
        status, data = status_for(payload)
        fake = FakeRunner()
        fake.statuses = [dict(status), dict(status)]
        fake.snapshots[status["generation"]] = data
        temp_parent = pathlib.Path(tempfile.gettempdir()) / "storage viz ; tmp"
        temp_parent.mkdir(exist_ok=True)
        tx = OpenSshTransport(runner=fake, temp_dir=temp_parent)

        fetched_status, fetched_data = tx.fetch_snapshot(server())

        self.assertEqual(fetched_status, status)
        self.assertEqual(fetched_data, data)
        self.assertEqual(fake.seen_temp_modes, [0o600, 0o600, 0o600])
        self.assertEqual(fake.seen_temp_is_regular, [True, True, True])
        self.assertEqual(len(fake.calls), 3)
        for call in fake.calls:
            self.assertFalse(call["shell"])
            self.assertIn("-o", call["argv"])
            joined = "\0".join(call["argv"])
            self.assertIn("BatchMode=yes", joined)
            self.assertIn("StrictHostKeyChecking=yes", joined)
            self.assertIn("IdentitiesOnly=yes", joined)
            self.assertIn("ConnectTimeout=", joined)
            self.assertIn("UserKnownHostsFile=/etc/storage-viz/known_hosts", joined)
            self.assertIn("User=monitoring", joined)
            self.assertIn("/etc/storage-viz/alpha.key", joined)
            self.assertNotIn("ProxyCommand", joined)
            self.assertNotIn("monitoring@", joined)
        status_batch = fake.calls[0]["input"].decode("utf-8")
        self.assertRegex(status_batch, r"^get /var/lib/storage-viz/scan-status\.json .+\n$")
        self.assertTrue("\\ " in status_batch or "'" in status_batch, "local temp path with spaces must be quoted/escaped")
        snapshot_batch = fake.calls[1]["input"].decode("utf-8")
        self.assertRegex(snapshot_batch, r"^get /var/lib/storage-viz/snapshots/alpha-1-1719200000-v1\.json .+\n$")
        self.assertTrue("\\ " in snapshot_batch or "'" in snapshot_batch, "snapshot local temp path with spaces must be quoted/escaped")
        temp_path = shlex.split(snapshot_batch)[2]
        self.assertFalse(os.path.exists(temp_path), "temporary download file must be cleaned")

    def test_rejects_generation_batch_injection_before_snapshot_get(self):
        from collector.transport import OpenSshTransport, TransportError

        good = dict(status_for(base_payload())[0])
        for bad_generation in ["../evil.json", "alpha-1-1-v1.json\nget /etc/passwd x", "alpha-1-1-v1.json;rm", "alpha 1.json"]:
            fake = FakeRunner(); fake.statuses = [dict(good, generation=bad_generation)]
            with self.subTest(bad_generation=bad_generation):
                with self.assertRaises(TransportError):
                    OpenSshTransport(runner=fake).fetch_snapshot(server())
                self.assertEqual(len(fake.calls), 1)

    def test_digest_race_rejected_and_temp_cleaned_without_leaking_paths(self):
        from collector.transport import OpenSshTransport, TransportError

        payload = base_payload()
        status, data = status_for(payload)
        raced = dict(status, sha256="b" * 64)
        fake = FakeRunner(); fake.statuses = [status, raced]; fake.snapshots[status["generation"]] = data
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_snapshot(server())
        self.assertIn("RACE", ctx.exception.code)
        self.assertNotIn("/var/lib", str(ctx.exception))
        temp_path = shlex.split(fake.calls[1]["input"].decode())[2]
        self.assertFalse(os.path.exists(temp_path))

    def test_status_must_be_small_json_object_and_process_errors_are_bounded(self):
        from collector.transport import OpenSshTransport, TransportError

        fake = FakeRunner(); fake.statuses = [[]]
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_status(server())
        self.assertIn("MALFORMED", ctx.exception.code)
        fake = FakeRunner(); fake.fail_next = TimeoutError("/secret/path timeout")
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_status(server())
        self.assertEqual(ctx.exception.code, "TIMEOUT")
        self.assertNotIn("/secret", str(ctx.exception))

    def test_rescan_uses_only_fixed_systemctl_start_command(self):
        from collector.transport import OpenSshTransport

        fake = FakeRunner()
        OpenSshTransport(runner=fake).start_rescan(server())
        argv = fake.calls[0]["argv"]
        dash_index = argv.index("--")
        self.assertLess(dash_index, len(argv) - 2)
        self.assertEqual(argv[dash_index + 1], "alpha.example.test")
        self.assertEqual(argv[dash_index + 2:], ["sudo", "-n", "/usr/bin/systemctl", "start", "storage-viz-scan.service"])
        self.assertIn("User=monitoring", "\0".join(argv))
        self.assertFalse(fake.calls[0]["shell"])

    def test_snapshot_size_is_checked_before_download_and_generation_must_match_server(self):
        from collector.transport import MAX_SNAPSHOT_BYTES, OpenSshTransport, TransportError

        good = dict(status_for(base_payload())[0])
        for bad_size in [True, -1, 0, MAX_SNAPSHOT_BYTES + 1]:
            fake = FakeRunner(); fake.statuses = [dict(good, byte_size=bad_size)]
            with self.subTest(bad_size=bad_size):
                with self.assertRaises(TransportError) as ctx:
                    OpenSshTransport(runner=fake).fetch_snapshot(server())
                self.assertIn(ctx.exception.code, {"BAD_STATUS", "SNAPSHOT_TOO_LARGE"})
                self.assertEqual(len(fake.calls), 1)

        fake = FakeRunner(); fake.statuses = [dict(good, generation="beta-2-1719200000-v1.json")]
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_snapshot(server())
        self.assertEqual(ctx.exception.code, "BAD_GENERATION")
        self.assertEqual(len(fake.calls), 1)

    def test_sftp_children_receive_disk_write_rlimit_caps_and_sigxfsz_maps_to_size_errors(self):
        from collector.transport import OpenSshTransport, TransportError

        payload = base_payload(); status, data = status_for(payload)
        fake = FakeRunner(); fake.statuses = [dict(status), dict(status)]; fake.snapshots[status["generation"]] = data
        tx = OpenSshTransport(runner=fake, max_status_bytes=1234, max_snapshot_bytes=4096)
        tx.fetch_snapshot(server())
        self.assertEqual([getattr(c["preexec_fn"], "limit_bytes", None) for c in fake.calls], [1234, status["byte_size"], 1234])

        fake = FakeRunner(); fake.statuses = [dict(status)]; fake.returncode_next = -signal.SIGXFSZ
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake, max_status_bytes=10).fetch_status(server())
        self.assertEqual(ctx.exception.code, "STATUS_TOO_LARGE")

        fake = FakeRunner(); fake.statuses = [dict(status)]; fake.snapshots[status["generation"]] = data; fake.returncode_next = -signal.SIGXFSZ
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_snapshot(server(), expected_status=status)
        self.assertEqual(ctx.exception.code, "SNAPSHOT_TOO_LARGE")

    @unittest.skipIf(os.name != "posix", "RLIMIT_FSIZE is POSIX-only")
    def test_rlimit_preexec_prevents_local_child_from_growing_file_beyond_cap(self):
        from collector.transport import _fsize_limiter

        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "limited.bin"
            script = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'x' * 4096)"
            result = subprocess.run([sys.executable, "-c", script, str(target)], shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=_fsize_limiter(1024))
            self.assertNotEqual(result.returncode, 0)
            self.assertLessEqual(target.stat().st_size, 1024)

    def test_nonzero_sftp_at_cap_maps_to_limit_but_below_cap_stays_unreachable(self):
        from collector.transport import OpenSshTransport, TransportError

        payload = base_payload(); status, data = status_for(payload)
        fake = FakeRunner(); fake.statuses = [dict(status)]; fake.snapshots[status["generation"]] = data
        fake.returncode_next = 1; fake.truncate_to_cap_on_nonzero = True
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake, max_status_bytes=10).fetch_status(server())
        self.assertEqual(ctx.exception.code, "STATUS_TOO_LARGE")

        fake = FakeRunner(); fake.statuses = [dict(status)]; fake.snapshots[status["generation"]] = data
        fake.returncode_next = 1; fake.truncate_to_cap_on_nonzero = True
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_snapshot(server(), expected_status=status)
        self.assertEqual(ctx.exception.code, "SNAPSHOT_TOO_LARGE")

        fake = FakeRunner(); fake.statuses = [dict(status)]; fake.returncode_next = 1
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_status(server())
        self.assertEqual(ctx.exception.code, "UNREACHABLE")

    def test_status_zero_byte_size_is_bad_status_but_max_boundary_is_allowed(self):
        from collector.transport import MAX_SNAPSHOT_BYTES, OpenSshTransport, TransportError

        status = dict(status_for(base_payload())[0])
        fake = FakeRunner(); fake.statuses = [dict(status, byte_size=0)]
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_snapshot(server())
        self.assertEqual(ctx.exception.code, "BAD_STATUS")
        self.assertEqual(len(fake.calls), 1)

        payload = b"{}"
        max_status = dict(status, byte_size=MAX_SNAPSHOT_BYTES)
        fake = FakeRunner(); fake.statuses = [dict(max_status)]; fake.snapshots[max_status["generation"]] = payload
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_snapshot(server(), expected_status=max_status)
        self.assertIn(ctx.exception.code, {"SIZE_MISMATCH", "RACE"})

    def test_generation_prefix_collision_uses_greedy_server_id_and_timestamp_equality(self):
        from collector.transport import OpenSshTransport, TransportError

        alpha_payload = base_payload(server_id="alpha", generation="alpha-1719200000-v1")
        alpha_status, alpha_data = status_for(alpha_payload)
        fake = FakeRunner(); fake.statuses = [dict(alpha_status), dict(alpha_status)]; fake.snapshots[alpha_status["generation"]] = alpha_data
        OpenSshTransport(runner=fake).fetch_snapshot(Server(
            id="alpha", display_name="Alpha", order=1, host="alpha.example.test", port=22, enabled=True,
            username="monitoring", identity_file=pathlib.PurePosixPath("/etc/storage-viz/alpha.key"),
            known_hosts_file=pathlib.PurePosixPath("/etc/storage-viz/known_hosts"), scanner={}, scanner_digest="a" * 64))

        collision = dict(alpha_status, generation="alpha-1-1719200000-v1.json", server_id="alpha")
        fake = FakeRunner(); fake.statuses = [collision]
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_snapshot(Server(
                id="alpha", display_name="Alpha", order=1, host="alpha.example.test", port=22, enabled=True,
                username="monitoring", identity_file=pathlib.PurePosixPath("/etc/storage-viz/alpha.key"),
                known_hosts_file=pathlib.PurePosixPath("/etc/storage-viz/known_hosts"), scanner={}, scanner_digest="a" * 64))
        self.assertEqual(ctx.exception.code, "BAD_GENERATION")
        self.assertEqual(len(fake.calls), 1)

        alpha1_payload = base_payload(server_id="alpha-1", generation="alpha-1-1719200000-v1")
        alpha1_status, alpha1_data = status_for(alpha1_payload)
        fake = FakeRunner(); fake.statuses = [dict(alpha1_status), dict(alpha1_status)]; fake.snapshots[alpha1_status["generation"]] = alpha1_data
        fetched_status, fetched_data = OpenSshTransport(runner=fake).fetch_snapshot(server())
        self.assertEqual(fetched_status["server_id"], "alpha-1")
        self.assertEqual(fetched_data, alpha1_data)

        bad_started = dict(alpha1_status, scan_started_unix=1719200001)
        fake = FakeRunner(); fake.statuses = [bad_started]
        with self.assertRaises(TransportError) as ctx:
            OpenSshTransport(runner=fake).fetch_snapshot(server())
        self.assertEqual(ctx.exception.code, "BAD_GENERATION")
        self.assertEqual(len(fake.calls), 1)

    def test_separate_timeouts_for_status_snapshot_and_rescan_allow_bounded_hours(self):
        from collector.transport import OpenSshTransport

        payload = base_payload(); status, data = status_for(payload)
        fake = FakeRunner(); fake.statuses = [dict(status), dict(status)]; fake.snapshots[status["generation"]] = data
        tx = OpenSshTransport(runner=fake, status_timeout_seconds=7, snapshot_timeout_seconds=8, rescan_timeout_seconds=7200)
        tx.fetch_snapshot(server())
        tx.start_rescan(server())
        self.assertEqual([c["timeout"] for c in fake.calls], [7, 8, 7, 7200])
        with self.assertRaises(ValueError):
            OpenSshTransport(runner=fake, rescan_timeout_seconds=24 * 60 * 60 + 1)


if __name__ == "__main__":
    unittest.main()
