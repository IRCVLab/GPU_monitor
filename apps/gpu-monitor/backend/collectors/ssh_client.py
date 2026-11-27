"""SSH connection wrapper using Paramiko."""
import io
import logging
import threading
import time
from concurrent.futures import Future, wait

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
        self._client_lock = threading.Lock()

    def connect(self, *, handshake_timeout: float | None = None) -> None:
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
        if handshake_timeout is not None:
            common.update(timeout=handshake_timeout, banner_timeout=handshake_timeout, auth_timeout=handshake_timeout)

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

        with self._client_lock:
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

    def run(self, command: str, timeout: float = 10) -> str:
        """Return stdout within the command deadline, or raise on failure."""
        exit_code, stdout, stderr = self._run_command(command, timeout, "SSH command deadline exceeded.")
        if exit_code != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Command exited with code {exit_code}. stderr: {err!r}"
            )
        return stdout.decode("utf-8", errors="replace").strip()

    def run_bounded(self, command: str, timeout: float = 15) -> str:
        """Run a hardware command with a deadline and a combined output limit."""
        code, stdout, _ = self._run_command(
            command, timeout, "Hardware command deadline exceeded.", output_limit=1_000_000,
        )
        if code != 0:
            raise RuntimeError("Hardware command failed.")
        return stdout.decode("utf-8", errors="replace").strip()

    def _run_command(
        self, command: str, timeout: float, timeout_error: str, *, output_limit: int | None = None,
    ) -> tuple[int, bytearray, bytearray]:
        with self._client_lock:
            client = self._client
        if client is None:
            raise RuntimeError("SSH client is not connected.")

        deadline = time.monotonic() + timeout
        cancelled = threading.Event()
        result: Future[tuple[int, bytearray, bytearray]] = Future()

        def run_on_transport() -> None:
            try:
                result.set_result(self._read_command(client, command, deadline, cancelled, output_limit))
            except Exception as exc:
                result.set_exception(exc)

        # Paramiko can block on request acknowledgement and on cleanup.
        threading.Thread(target=run_on_transport, name="ssh-command", daemon=True).start()
        done, _ = wait([result], timeout=max(0, deadline - time.monotonic()))
        if not done:
            cancelled.set()
            self._discard_transport(client)
            raise TimeoutError(timeout_error)
        try:
            return result.result()
        except (paramiko.SSHException, OSError, EOFError):
            self._discard_transport(client)
            raise

    @staticmethod
    def _read_command(
        client: paramiko.SSHClient, command: str, deadline: float,
        cancelled: threading.Event, output_limit: int | None,
    ) -> tuple[int, bytearray, bytearray]:
        transport = client.get_transport()
        if transport is None:
            raise paramiko.SSHException("SSH transport unavailable.")

        channel = transport.open_session(timeout=max(0, deadline - time.monotonic()))
        try:
            if cancelled.is_set():
                raise TimeoutError()
            channel.settimeout(0)
            channel.exec_command(command)
            stdout, stderr = bytearray(), bytearray()
            streams = {channel.recv: stdout, channel.recv_stderr: stderr}
            while not cancelled.is_set():
                made_progress = False
                for receive, output in streams.copy().items():
                    try:
                        chunk = receive(65536)
                    except TimeoutError:
                        continue
                    if not chunk:
                        del streams[receive]
                        continue
                    output.extend(chunk)
                    made_progress = True

                if output_limit is not None and len(stdout) + len(stderr) > output_limit:
                    raise RuntimeError("Hardware command output limit exceeded.")
                if not streams and channel.exit_status_ready():
                    return channel.recv_exit_status(), stdout, stderr
                if not made_progress:
                    cancelled.wait(0.01)
            raise TimeoutError()
        finally:
            channel.close()

    def _discard_transport(self, client: paramiko.SSHClient) -> None:
        with self._client_lock:
            if self._client is not client:
                return
            self._client = None
        # Let the next collection reconnect while any old I/O unwinds.
        threading.Thread(target=client.close, name="ssh-close", daemon=True).start()

    def close(self) -> None:
        with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            client.close()
            logger.debug("SSH closed for %s", self._server.name)

    @property
    def is_connected(self) -> bool:
        with self._client_lock:
            client = self._client
        if client is None:
            return False
        transport = client.get_transport()
        return transport is not None and transport.is_active()
