import unittest
from unittest.mock import Mock
from types import SimpleNamespace

import paramiko

from backend.collectors.ssh_client import SSHClient


class SSHRecoveryTests(unittest.TestCase):
    def make_ssh(self):
        client = Mock()
        client.get_transport.return_value.is_active.return_value = True
        ssh = SSHClient(SimpleNamespace(name='Shannon'))
        ssh._client = client
        return ssh, client

    def test_channel_open_failure_invalidates_transport_so_collector_reconnects(self):
        ssh, client = self.make_ssh()
        client.exec_command.side_effect = paramiko.ChannelException(2, 'Connect failed')
        with self.assertRaises(paramiko.ChannelException):
            ssh.run('nvidia-smi')
        client.close.assert_called_once()
        self.assertFalse(ssh.is_connected)

    def test_command_failure_keeps_transport_for_fallback_command(self):
        ssh, client = self.make_ssh()
        stdout, stderr = Mock(), Mock()
        client.exec_command.return_value = (Mock(), stdout, stderr)
        stdout.channel.recv_exit_status.return_value = 127
        stdout.read.return_value = b''
        stderr.read.return_value = b'gpustat: command not found'
        with self.assertRaises(RuntimeError):
            ssh.run('gpustat --json')
        client.close.assert_not_called()
        self.assertTrue(ssh.is_connected)
