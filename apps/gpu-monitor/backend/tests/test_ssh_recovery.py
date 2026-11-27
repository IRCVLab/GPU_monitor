import asyncio
import io
import threading
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import paramiko

from backend.collectors import server_collector, storage
from backend.collectors.ssh_client import SSHClient


class SshConnectionTests(unittest.TestCase):
    def test_callers_can_bound_tcp_banner_and_authentication(self):
        server = SimpleNamespace(
            name="fixture", host="fixture", port=22, ssh_user="monitor",
            ssh_private_key=None, ssh_password="encrypted",
        )
        with (
            patch("backend.collectors.ssh_client.paramiko.SSHClient") as client_factory,
            patch("backend.collectors.ssh_client.decrypt", return_value="fixture-password"),
        ):
            ssh = SSHClient(server)
            ssh.connect(handshake_timeout=4)
            self.addCleanup(ssh.close)

        client_factory.return_value.connect.assert_called_once_with(
            hostname="fixture", port=22, username="monitor", password="fixture-password",
            timeout=4, banner_timeout=4, auth_timeout=4,
        )


class CommandChannel:
    def __init__(self, stdout=b"", stderr=b"", code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.code = code
        self.closed = threading.Event()

    def settimeout(self, timeout):
        pass

    def exec_command(self, command):
        pass

    def recv_ready(self):
        return bool(self.stdout)

    def recv_stderr_ready(self):
        return bool(self.stderr)

    def recv(self, size):
        data, self.stdout = self.stdout[:size], self.stdout[size:]
        return data

    def recv_stderr(self, size):
        data, self.stderr = self.stderr[:size], self.stderr[size:]
        return data

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self.code

    def close(self):
        self.closed.set()


class WindowLimitedChannel(CommandChannel):
    def exit_status_ready(self):
        return not self.stdout and not self.stderr

    def recv_exit_status(self):
        if not self.exit_status_ready():
            raise AssertionError("Both streams must be drained before exit status is available")
        return self.code


class HungChannel(CommandChannel):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase
        self.released = threading.Event()

    def exec_command(self, command):
        if self.phase == "ack":
            self.released.wait(2)

    def recv(self, size):
        if self.phase == "stdout":
            self.released.wait(2)
        return super().recv(size)

    def recv_stderr(self, size):
        if self.phase == "stderr":
            self.released.wait(2)
        return super().recv_stderr(size)

    def exit_status_ready(self):
        return self.phase != "exit"

    def recv_exit_status(self):
        if not self.exit_status_ready():
            self.released.wait(2)
        return self.code

    def close(self):
        self.released.wait(2)
        super().close()


def ssh_transport(*channels):
    client = Mock()
    transport = client.get_transport.return_value
    transport.is_active.return_value = True
    transport.open_session.side_effect = channels

    def legacy_exec(command, timeout):
        channel = transport.open_session(timeout=timeout)
        channel.exec_command(command)
        stdout = io.BytesIO(channel.stdout)
        stdout.channel = channel
        return None, stdout, io.BytesIO(channel.stderr)

    client.exec_command.side_effect = legacy_exec
    return client


class SSHRecoveryTests(unittest.TestCase):
    def make_ssh(self):
        ssh = SSHClient(SimpleNamespace(name='Shannon'))
        return ssh

    def test_command_drains_both_streams_before_waiting_for_exit(self):
        ssh = self.make_ssh()
        stdout = b"x" * 2_500_000
        channel = WindowLimitedChannel(stdout=stdout, stderr=b"y" * 2_500_000)
        ssh._client = ssh_transport(channel)
        self.assertEqual(ssh.run("collect", timeout=1), stdout.decode())
        self.assertTrue(channel.closed.is_set())

    def test_command_drains_output_sent_after_exit_status(self):
        class LateOutputChannel(CommandChannel):
            def __init__(self):
                super().__init__()
                self.reads = 0

            def recv(self, size):
                self.reads += 1
                if self.reads == 1:
                    raise TimeoutError()
                if self.reads == 2:
                    return b"final output"
                return b""

        ssh = self.make_ssh()
        ssh._client = ssh_transport(LateOutputChannel())
        self.assertEqual(ssh.run("collect", timeout=0.5), "final output")

    def test_command_deadline_covers_ack_output_exit_and_channel_cleanup(self):
        for phase in ("ack", "stdout", "stderr", "exit", "cleanup"):
            with self.subTest(phase=phase):
                ssh = self.make_ssh()
                channel = HungChannel(phase)
                self.addCleanup(channel.released.set)
                ssh._client = ssh_transport(channel)
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    ssh.run("collect", timeout=0.03)
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertFalse(ssh.is_connected)
                channel.released.set()

    def test_command_deadline_covers_open_and_cannot_close_replacement_transport(self):
        ssh = self.make_ssh()
        original = ssh_transport()
        close_started = threading.Event()
        release_close = threading.Event()
        self.addCleanup(release_close.set)

        def close():
            close_started.set()
            release_close.wait(2)

        def open_session(timeout):
            release_close.wait(2)
            raise paramiko.SSHException("old session closed")

        original.close.side_effect = close
        transport = original.get_transport.return_value
        transport.open_session.side_effect = open_session
        ssh._client = original
        with self.assertRaises(TimeoutError):
            ssh.run("collect", timeout=0.03)
        self.assertTrue(close_started.wait(0.5))
        replacement = ssh_transport(CommandChannel(stdout=b"next round"))
        ssh._client = replacement
        self.assertEqual(ssh.run("collect", timeout=0.5), "next round")
        release_close.set()
        self.assertTrue(ssh.is_connected)
        replacement.close.assert_not_called()

    def test_channel_open_failure_invalidates_transport_so_collector_reconnects(self):
        ssh = self.make_ssh()
        client = ssh_transport(paramiko.ChannelException(2, 'Connect failed'))
        closed = threading.Event()
        client.close.side_effect = closed.set
        ssh._client = client
        with self.assertRaises(paramiko.ChannelException):
            ssh.run('nvidia-smi')
        self.assertTrue(closed.wait(0.5))
        client.close.assert_called_once()
        self.assertFalse(ssh.is_connected)

    def test_command_failure_keeps_transport_for_fallback_command(self):
        ssh = self.make_ssh()
        client = ssh_transport(CommandChannel(stderr=b'gpustat: command not found', code=127),
                               CommandChannel(stdout=b"nvidia data"))
        ssh._client = client
        with self.assertRaisesRegex(RuntimeError, "code 127.*gpustat: command not found"):
            ssh.run('gpustat --json')
        client.close.assert_not_called()
        self.assertTrue(ssh.is_connected)
        self.assertEqual(ssh.run("nvidia-smi"), "nvidia data")

    def test_hardware_command_preserves_combined_output_limit(self):
        ssh = self.make_ssh()
        channel = CommandChannel(stdout=b"x" * 500_001, stderr=b"y" * 500_000)
        ssh._client = ssh_transport(channel)
        with self.assertRaisesRegex(RuntimeError, "Hardware command output limit exceeded"):
            ssh.run_bounded("read sensors", timeout=1)
        self.assertTrue(channel.closed.is_set())
        self.assertTrue(ssh.is_connected)

    def test_storage_deadline_preserves_last_snapshot_and_observation_time(self):
        server = SimpleNamespace(id=1, name="Fixture")
        collector = server_collector.ServerCollector(server)
        collector._storage = storage.StorageCollector(cache_ttl_seconds=0)
        raw = b'{"summary":{"mount_count":1,"total":1024,"used":512,"percent":50},"mounts":[{"mount":"/data"}]}'
        channel = HungChannel("exit")
        self.addCleanup(channel.released.set)
        collector._ssh._client = ssh_transport(CommandChannel(stdout=raw), channel)
        run = collector._ssh.run
        first_time = datetime(2026, 11, 23, tzinfo=timezone.utc)
        later = datetime(2026, 11, 24, tzinfo=timezone.utc)
        deadlines = iter((0.5, 0.03))
        with (patch.object(collector._ssh, "run", side_effect=lambda command, timeout: run(command, next(deadlines))),
              patch.object(storage, "_utcnow", side_effect=[first_time, later])):
            original = collector._sync_collect_storage()
            cached = collector._sync_collect_storage()
        self.assertEqual(original["summary"]["used"], 512)
        self.assertEqual(original["collected_at"], first_time.isoformat())
        self.assertEqual(cached, original)
        self.assertFalse(collector._ssh.is_connected)


class CollectorRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_gpu_command_timeout_recovers_in_the_next_collection_cycle(self):
        server = SimpleNamespace(id=1, name="Fixture", host="gpu-test.invalid", port=22,
                                 ssh_user="monitor", ssh_password="encrypted", ssh_private_key=None)
        collector = server_collector.ServerCollector(server)
        collector._historical_gpu_indices_loaded = True
        hung = HungChannel("exit")
        self.addCleanup(hung.released.set)
        original = ssh_transport(hung)
        original.close.side_effect = hung.released.set
        replacement = ssh_transport(
            CommandChannel(stdout=b'{"gpus":[]}'),
            CommandChannel(stdout=b"1.0,1048576,2097152,0.0,0.0,0,1000,2000,10.0,0"),
            CommandChannel(stdout=b'{"summary":{},"mounts":[]}'),
        )
        collector._ssh._client = original
        observed = []
        recovered = asyncio.Event()

        async def broadcast(data):
            observed.append((collector.status, collector.status_reason, data))
            if collector.status == "online":
                recovered.set()

        with (patch.object(collector, "_broadcast", side_effect=broadcast),
              patch.object(collector, "_should_archive", return_value=False),
              patch.object(collector, "_should_cleanup", return_value=False),
              patch.object(server_collector, "_RETRY_SLEEP", 0),
              patch.object(server_collector, "log_event", new_callable=AsyncMock),
              patch("backend.collectors.ssh_client.decrypt", return_value="fixture"),
              patch("backend.collectors.ssh_client.paramiko.SSHClient", return_value=replacement)):
            run = collector._ssh.run
            deadlines = iter((0.03, 0.5, 0.5, 0.5))
            with patch.object(collector._ssh, "run", side_effect=lambda command, timeout=10: run(command, next(deadlines))):
                task = asyncio.create_task(collector.run())
                try:
                    await asyncio.wait_for(recovered.wait(), 1)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

        self.assertEqual([status for status, _, _ in observed], ["degraded", "online"])
        self.assertEqual(observed[0][1]["code"], "timeout")
        self.assertIsNone(observed[1][1])
        self.assertIsNotNone(observed[1][2]["system"])
        replacement.connect.assert_called_once()
