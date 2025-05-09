# monitor.py

import threading
import time
import json
import logging
import paramiko
from tenacity import retry, stop_after_attempt, wait_fixed
from flask import Flask, jsonify

# 1) 전역 로그 설정: 콘솔에 DEBUG 이상 출력, 시간·레벨·메시지 포함
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

class ServerMonitor:
    def __init__(self, alias, host, port, user, passwd):
        self.alias = alias
        self.logger = logging.getLogger(f"Monitor.{alias}")
        self.host, self.port, self.user, self.passwd = host, port, user, passwd

        self.client = None
        self.lock = threading.Lock()
        self.data = None
        self.reload_event = threading.Event()

        self.logger.info(f"인스턴스 생성: {host}:{port} 사용자={user}")
        self._ensure_connection()
        threading.Thread(target=self._loop, daemon=True).start()

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(60))
    def _connect(self):
        self.logger.debug("SSH 연결 시도...")
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
        self.logger.info("SSH 연결 성공")
        return client

    def _ensure_connection(self):
        if self.client is None or not self.client.get_transport().is_active():
            try:
                self.client = self._connect()
            except Exception as e:
                self.logger.error(f"SSH 연결 실패: {e}", exc_info=True)
                self.client = None

    def _fetch_stats(self):
        self.logger.debug("gpustat 실행하여 데이터 수집...")
        stdin, stdout, stderr = self.client.exec_command("gpustat --json")
        text = stdout.read().decode()
        self.logger.debug(f"gpustat 결과: {text[:200]}...")  # 앞부분만 예시 출력
        return json.loads(text)

    def _loop(self):
        while True:
            self._ensure_connection()
            if self.client:
                try:
                    stats = self._fetch_stats()
                    with self.lock:
                        self.data = stats
                    self.logger.debug("데이터 갱신 완료")
                except Exception as e:
                    self.logger.warning(f"데이터 수집 오류, 재연결 필요: {e}", exc_info=True)
                    try:
                        self.client.close()
                    except:
                        pass
                    self.client = None

            # 즉시 갱신 요청 대기 or 5초 주기
            if self.reload_event.wait(timeout=5):
                self.logger.info("Reload 이벤트 감지: 즉시 데이터 갱신")
                self.reload_event.clear()

    def get_data(self):
        with self.lock:
            return self.data

    def reload_now(self):
        self.logger.info("reload_now() 호출됨")
        self.reload_event.set()


# ─── hosts 리스트 정의 ───────────────────────────────────
User = 'shchoi'
Passwd = 'root'
hosts = [
    ("Poseidon", "166.104.167.164", 8989, User, Passwd),
    ("Hinton",  "166.104.167.164", 8990, User, Passwd),
    ("Turing",  "166.104.167.164", 8991, User, Passwd),
    ("lecun",   "166.104.167.164", 8992, User, Passwd),
]

monitors = {
    alias: ServerMonitor(alias, host, port, user, passwd)
    for alias, host, port, user, passwd in hosts
}

app = Flask(__name__)

@app.route("/stats")
def all_stats():
    return jsonify({alias: m.get_data() for alias, m in monitors.items()})

@app.route("/stats/<alias>")
def stats(alias):
    return jsonify(monitors.get(alias).get_data() or {})

@app.route("/reload/<alias>", methods=["POST"])
def reload(alias):
    monitors.get(alias).reload_now()
    return "", 204

if __name__ == "__main__":
    # Flask 자체 로그 레벨도 조절 가능합니다.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host="0.0.0.0", port=5001)