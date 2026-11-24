"""SSH connection wrapper using Paramiko."""
import io
import logging
import threading
import time

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

        try:
            _, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        except paramiko.SSHException:
            # An active transport can still refuse every new command channel.
            # Invalidate it so collection retries open a fresh SSH connection.
            self.close()
            raise
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()

        if exit_code != 0:
            err = stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Command exited with code {exit_code}. stderr: {err!r}"
            )

        return out

    def run_bounded(self, command: str, timeout: float = 15) -> str:
        """Hardware-only deadline, including command acknowledgement and exit.

        Channel timeouts do not bound Paramiko recv_exit_status(). Drain output
        while polling readiness, and close this dedicated connection at deadline
        to interrupt transport/channel waits as well.
        """
        client = self._client
        if client is None:
            raise RuntimeError("SSH client is not connected.")
        expired = threading.Event()
        deadline = time.monotonic() + timeout
        channel = None

        def abort():
            expired.set()
            client.close()

        watchdog = threading.Timer(timeout, abort)
        watchdog.daemon = True
        watchdog.start()
        try:
            transport = client.get_transport()
            if transport is None:
                raise RuntimeError("SSH transport unavailable.")
            channel = transport.open_session(timeout=timeout)
            channel.exec_command(command)
            output = bytearray()
            stderr_size = 0
            while True:
                if expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("Hardware command deadline exceeded.")
                if channel.recv_ready():
                    output.extend(channel.recv(65536))
                if channel.recv_stderr_ready():
                    stderr_size += len(channel.recv_stderr(65536))
                if len(output) + stderr_size > 1_000_000:
                    raise RuntimeError("Hardware command output limit exceeded.")
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    code = channel.recv_exit_status()
                    if expired.is_set():
                        raise TimeoutError("Hardware command deadline exceeded.")
                    if code != 0:
                        raise RuntimeError("Hardware command failed.")
                    return output.decode("utf-8", errors="replace").strip()
                time.sleep(min(0.02, max(0, deadline-time.monotonic())))
        except Exception:
            if expired.is_set():
                raise TimeoutError("Hardware command deadline exceeded.") from None
            raise
        finally:
            # Keep the watchdog armed until channel cleanup has also finished.
            try:
                if channel is not None:
                    channel.close()
            finally:
                watchdog.cancel()

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
