import threading
import time
import json
import socket
import logging
from logging.handlers import RotatingFileHandler
import datetime # Added datetime import

import paramiko
from tenacity import retry, stop_after_attempt, wait_fixed
from flask import Flask, jsonify
from flask_cors import CORS

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
    # 3번 전략: 몇 주기마다 강제 재연결할지 (5초 * 720 = 1시간)
    RECONNECT_INTERVAL = 720

    def __init__(self, alias, host, port, user, passwd):
        self.alias = alias
        self.logger = logging.getLogger(f"Monitor.{alias}")
        self.host = host
        self.port = port
        self.user = user
        self.passwd = passwd

        self.client = None
        self.lock = threading.Lock()
        # self.data will now store a dict: {'stats': ..., 'last_updated': ...} or None
        self.data = None
        self.reload_event = threading.Event()

        # 재연결 카운터 초기화
        self._reconnect_counter = 0

        self.logger.info(f"Initializing monitor: {host}:{port} (user={user})")
        self._ensure_connection()
        threading.Thread(target=self._loop, daemon=True).start()

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
                self.logger.error(f"SSH connect failed: {e}")
                self.client = None
                with self.lock:
                    self.data = None # Set data to None immediately on connection failure

    def _fetch_stats(self):
        stdin = stdout = stderr = None
        try:
            stdin, stdout, stderr = self.client.exec_command("gpustat --json")
            stdout.channel.settimeout(10.0)
            stderr.channel.settimeout(10.0)

            text = stdout.read().decode("utf-8", errors="ignore")
            _ = stderr.read()  # drain stderr

            return json.loads(text)

        except socket.timeout as e:
            self.logger.error(f"gpustat command timeout: {e}")
            # force reconnect next loop
            self.client.close()
            raise

        except json.JSONDecodeError as e:
            snippet = text[:200] if 'text' in locals() else ""
            self.logger.error(f"JSON parse error: {e} / payload: {snippet!r}")
            raise

        finally:
            # 2번 전략: 명시적 자원 해제
            try:
                if stdin:
                    stdin.close()
                if stdout:
                    stdout.close()
                if stderr:
                    stderr.close()
            except Exception as e:
                self.logger.warning(f"Error closing channels: {e}")

    def _loop(self):
        while True:
            self._ensure_connection()
            
            wait_interval = 15  # Default wait time is 15s for disconnected state

            if self.client:
                try:
                    stats = self._fetch_stats()
                    with self.lock:
                        # Store stats and current timestamp
                        self.data = {
                            "stats": stats,
                            "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                    self.logger.debug("Stats updated")
                    wait_interval = 5  # If successful, next fetch is in 5s

                    # 3번 전략: 주기적 재연결
                    self._reconnect_counter += 1
                    if self._reconnect_counter >= self.RECONNECT_INTERVAL:
                        self.logger.info("Performing scheduled reconnect to free resources")
                        try:
                            self.client.close()
                        except Exception:
                            pass
                        self.client = None
                        self._reconnect_counter = 0

                except Exception:
                    # on any error, drop client so _ensure_connection will reconnect
                    try:
                        self.client.close()
                    except Exception:
                        pass
                    self.client = None
                    # Also clear data on fetch error
                    with self.lock:
                        self.data = None
            else:
                # If connection is not established, ensure data is None
                with self.lock:
                    self.data = None

            # wait for reload event or the calculated interval
            if self.reload_event.wait(timeout=wait_interval):
                self.logger.info("Manual reload triggered")
                self.reload_event.clear()

    def get_data(self):
        with self.lock:
            return self.data

    def reload_now(self):
        self.logger.info("reload_now() called")
        self.reload_event.set()


# ─── Hosts Configuration ─────────────────────────────────────────────────────
User = "monitoring"
Passwd = "123123"
Address = "166.104.167.11"
hosts = [
    ("00Poseidon", Address, 2201, User, Passwd),
    ("01Hinton",   Address, 2202, User, Passwd),
    ("02Turing",   Address, 2203, User, Passwd),
    ("03Lecun",    Address, 2204, User, Passwd),
    ("04ACE",      Address, 2205, User, Passwd),
    ("04NEO",      Address, 2206, User, Passwd),
]


def create_monitors():
    return {
        alias: ServerMonitor(alias, host, port, user, passwd)
        for alias, host, port, user, passwd in hosts
    }


# ─── Flask App ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
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
    monitors = create_monitors()
    app.run(host="0.0.0.0", port=5001)
