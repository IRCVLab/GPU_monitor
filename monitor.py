import json
import threading
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from pathlib import Path

import paramiko
from flask import Flask, jsonify, request
from flask_cors import CORS

from monitoring_core.collectors import (
    CPUCollector,
    CollectorRegistry,
    GPUCollector,
    StorageCollector,
)

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


class NoteStore:
    """Simple JSON-backed store for per-server notes."""

    def __init__(self, path="notes_store.json"):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            return {}

    def _save_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as fp:
            json.dump(self.data, fp, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)

    def get_note(self, alias):
        with self.lock:
            note = self.data.get(alias)
            return dict(note) if note else None

    def set_note(self, alias, content):
        content = (content or "").strip()
        with self.lock:
            if not content:
                if alias in self.data:
                    self.data.pop(alias, None)
                    self._save_locked()
                return None
            note = {
                "content": content[:500],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.data[alias] = note
            self._save_locked()
            return note

    def delete_note(self, alias):
        with self.lock:
            if self.data.pop(alias, None) is not None:
                self._save_locked()


# ─── ServerMonitor Definition ────────────────────────────────────────────────
class ServerMonitor:
    # 3번 전략: 몇 주기마다 강제 재연결할지 (5초 * 720 = 1시간)
    RECONNECT_INTERVAL = 720

    def __init__(self, alias, host, port, user, passwd, registry: CollectorRegistry):
        self.alias = alias
        self.logger = logging.getLogger(f"Monitor.{alias}")
        self.host = host
        self.port = port
        self.user = user
        self.passwd = passwd
        self.registry = registry

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

    def _collect_snapshot(self):
        resource_results = self.registry.collect_all(self.client)
        resources = {}
        resource_errors = {}

        for name, result in resource_results.items():
            if result.payload is not None:
                resources[name] = result.payload
            if result.error:
                resource_errors[name] = result.error

        if resources and not resource_errors:
            status = "online"
        elif resources:
            status = "degraded"
        elif resource_errors:
            status = "error"
        else:
            status = "offline"

        snapshot = {
            "alias": self.alias,
            "resources": resources,
            "errors": resource_errors or None,
            "metadata": {"host": self.host, "port": self.port},
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "status": status,
        }
        return snapshot

    def _loop(self):
        while True:
            self._ensure_connection()
            
            wait_interval = 15  # Default wait time is 15s for disconnected state

            if self.client:
                try:
                    snapshot = self._collect_snapshot()
                    with self.lock:
                        self.data = snapshot
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
    ("05NEO",      Address, 2206, User, Passwd),
]

note_store = NoteStore()


def build_registry() -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(GPUCollector())
    registry.register(CPUCollector())
    registry.register(StorageCollector())
    return registry


def create_monitors():
    return {
        alias: ServerMonitor(alias, host, port, user, passwd, build_registry())
        for alias, host, port, user, passwd in hosts
    }


# ─── Flask App ───────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
monitors = {}  # filled in __main__


@app.route("/stats")
def all_stats():
    response = {}
    for alias, monitor in monitors.items():
        snapshot = monitor.get_data()
        payload = {}
        if snapshot:
            payload.update(snapshot)
        payload["note"] = note_store.get_note(alias)
        response[alias] = payload
    return jsonify(response)


@app.route("/stats/<alias>")
def stats(alias):
    m = monitors.get(alias)
    if not m:
        return jsonify({})
    snapshot = m.get_data()
    payload = {}
    if snapshot:
        payload.update(snapshot)
    payload["note"] = note_store.get_note(alias)
    return jsonify(payload)


@app.route("/reload/<alias>", methods=["POST"])
def reload(alias):
    m = monitors.get(alias)
    if m:
        m.reload_now()
        return "", 204
    else:
        return "Alias not found", 404


@app.route("/notes/<path:alias>", methods=["GET", "POST", "DELETE"])
def notes(alias):
    if request.method == "GET":
        note = note_store.get_note(alias)
        return jsonify(note or {})

    if request.method == "DELETE":
        note_store.delete_note(alias)
        return "", 204

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        note_store.delete_note(alias)
        return "", 204

    note_store.set_note(alias, content)
    return "", 204


if __name__ == "__main__":
    monitors = create_monitors()
    app.run(host="0.0.0.0", port=5001)
