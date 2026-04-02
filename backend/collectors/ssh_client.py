"""SSH connection wrapper using Paramiko."""
import io
import logging

import paramiko
from paramiko import RSAKey, Ed25519Key, ECDSAKey

try:
    from ..crypto import decrypt
    from ..models import Server
except ImportError:  # pragma: no cover - direct execution fallback
    from crypto import decrypt
    from models import Server

logger = logging.getLogger(__name__)


class SSHClient:
    def __init__(self, server: Server) -> None:
        self._server = server
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        """Connect using password or private key from server model.

        Credentials are decrypted via crypto.decrypt() before use.
        Keepalive is set to 30 seconds after a successful connection.
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        common = dict(
            hostname=self._server.host,
            port=self._server.port or 22,
            username=self._server.ssh_user,
            timeout=10,
        )

        if self._server.ssh_private_key:
            key_str = decrypt(self._server.ssh_private_key)
            pkey = self._load_private_key(key_str)
            client.connect(**common, pkey=pkey)
        elif self._server.ssh_password:
            password = decrypt(self._server.ssh_password)
            client.connect(**common, password=password)
        else:
            raise RuntimeError(
                f"Server {self._server.name!r} has no SSH credentials configured."
            )

        transport = client.get_transport()
        if transport:
            transport.set_keepalive(30)

        self._client = client
        logger.debug("SSH connected to %s (%s)", self._server.name, self._server.host)

    @staticmethod
    def _load_private_key(key_str: str) -> paramiko.PKey:
        """Try RSAKey, then Ed25519Key, then ECDSAKey."""
        for key_cls in (RSAKey, Ed25519Key, ECDSAKey):
            try:
                return key_cls.from_private_key(io.StringIO(key_str))
            except paramiko.SSHException:
                continue
        raise RuntimeError("Unsupported or invalid private key format.")

    def run(self, command: str, timeout: int = 10) -> str:
        """Execute command and return stdout. Raises RuntimeError on failure."""
        if not self._client:
            raise RuntimeError("SSH client is not connected.")

        _, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()

        if exit_code != 0:
            err = stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Command exited with code {exit_code}. stderr: {err!r}"
            )

        return out

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            logger.debug("SSH closed for %s", self._server.name)

    @property
    def is_connected(self) -> bool:
        if not self._client:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()
