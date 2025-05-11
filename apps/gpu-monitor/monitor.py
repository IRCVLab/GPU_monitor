# monitor.py

import threading
import time
import json
import socket
import logging
from logging.handlers import RotatingFileHandler

import paramiko
from tenacity import retry, stop_after_attempt, wait_fixed
from flask import Flask, jsonify

# ─── Logging Configuration ────────────────────────────────────────────────────
logger = logging.getLogger()  # root logger
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    "%Y-%m-%d %H:%M:%S"
)

# Console handler (INFO+)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# Rotating file handler: 10MB, keep 5 backups
fh = RotatingFileHandler(
    filename="server.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8"
)
fh.setLevel(logging.INFO)
fh.setFormatter(formatter)
logger.addHandler(fh)

# Suppress Flask werkzeug access logs below WARNING
logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ─── ServerMonitor Definition ────────────────────────────────────────────────
class ServerMonitor:
    def __init__(self, alias, host, port, user, passwd):
        self.alias = alias
        self.logger = logging.getLogger(f"Monitor.{alias}")
        self.host = host
        self.port = port
        self.user = user
        self.passwd = passwd

        self.client = None
        self.lock = threading.Lock()
        self.data = None
        self.reload_event = threading.Event()

        self.logger.info(f"Initializing monitor: {host}:{port} (user={user})")
        self._ensure_connection()
        threading.Thread(target=self._loop, daemon=True).start()

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(60))
    def _connect(self):
        self.logger.info("Attempting SSH connection...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=self.passwd,
            timeout=10,
            allow_agent=False,
            look_for_keys=False
        )
        client.get_transport().set_keepalive(30)
        self.logger.info("SSH connection established")
        return client

    def _ensure_connection(self):
        if self.client is None or not self.client.get_transport().is_active():
            try:
                self.client = self._connect()
            except Exception as e:
                self.logger.error(f"SSH connect failed: {e}", exc_info=True)
                self.client = None

    def _fetch_stats(self):
        stdin = stdout = stderr = None
        try:
            stdin, stdout, stderr = self.client.exec_command("gpustat --json")
            # set a socket timeout on the channels so .read() won't block forever
            stdout.channel.settimeout(10.0)
            stderr.channel.settimeout(10.0)

            text = stdout.read().decode("utf-8", errors="ignore")
            _ = stderr.read()  # drain stderr to avoid blocking

            return json.loads(text)

        except socket.timeout as e:
            self.logger.error(f"gpustat command timeout: {e}")
            # force reconnect on next loop
            self.client.close()
            raise

        except json.JSONDecodeError as e:
            snippet = text[:200] if 'text' in locals() else ""
            self.logger.error(f"JSON parse error: {e} / payload: {snippet!r}")
            raise

        finally:
            # always clean up
            try:
                if stdin:  stdin.close()
                if stdout: stdout.channel.close()
                if stderr: stderr.channel.close()
            except Exception:
                pass

    def _loop(self):
        while True:
            self._ensure_connection()
            if self.client:
                try:
                    stats = self._fetch_stats()
                    with self.lock:
                        self.data = stats
                    self.logger.debug("Stats updated")
                except Exception:
                    # on any error, drop client so _ensure_connection will reconnect
                    try:
                        self.client.close()
                    except Exception:
                        pass
                    self.client = None

            # wait for reload event or 5s interval
            if self.reload_event.wait(timeout=5):
                self.logger.info("Manual reload triggered")
                self.reload_event.clear()

    def get_data(self):
        with self.lock:
            return self.data

    def reload_now(self):
        self.logger.info("reload_now() called")
        self.reload_event.set()


# ─── Hosts Configuration ─────────────────────────────────────────────────────
User = "shchoi"
Passwd = "root"
hosts = [
    ("Poseidon", "166.104.167.164", 8989, User, Passwd),
    ("Hinton",   "166.104.167.164", 8990, User, Passwd),
    ("Turing",   "166.104.167.164", 8991, User, Passwd),
    ("lecun",    "166.104.167.164", 8992, User, Passwd),
]


def create_monitors():
    return {
        alias: ServerMonitor(alias, host, port, user, passwd)
        for alias, host, port, user, passwd in hosts
    }


# ─── Flask App ───────────────────────────────────────────────────────────────
app = Flask(__name__)
monitors = {}  # filled in __main__


@app.route("/stats")
def all_stats():
    return jsonify({alias: m.get_data() for alias, m in monitors.items()})


@app.route("/stats/<alias>")
def stats(alias):
    m = monitors.get(alias)
    return jsonify(m.get_data() if m else {})


@app.route("/reload/<alias>", methods=["POST"])
def reload(alias):
    m = monitors.get(alias)
    if m:
        m.reload_now()
        return "", 204
    else:
        return "Alias not found", 404


if __name__ == "__main__":
    # Only when run directly do we spin up threads & Flask
    monitors = create_monitors()
    app.run(host="0.0.0.0", port=5001)