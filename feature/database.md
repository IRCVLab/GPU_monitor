# 데이터베이스 설계

## 기술 선택: SQLite

- PostgreSQL 불필요 (단일 서버, ~10대 GPU 서버, ~30명 사용자 규모)
- 단일 파일 → 백업 단순 (`cp gpu_monitor.db gpu_monitor.db.bak`)
- WAL 모드로 concurrent read/write 지원
- 복잡한 time-series 압축 불필요 (7일만 보관)

---

## 스키마

### servers

```sql
CREATE TABLE servers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,             -- 표시 이름 (e.g. "Poseidon")
    host            TEXT NOT NULL,             -- IP or hostname
    port            INTEGER DEFAULT 22,
    ssh_user        TEXT NOT NULL,
    ssh_password    TEXT,                      -- Fernet 암호화
    ssh_private_key TEXT,                      -- Fernet 암호화
    network         TEXT DEFAULT 'internal',   -- 'internal' | 'external'
    display_order   INTEGER DEFAULT 0,
    registered_by   TEXT,                              -- username (문자열, 계정 없으므로)
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### server_status

서버 상태 (수집 루프에서 실시간 업데이트, DB 아닌 메모리 우선 — 5초마다 갱신).

```sql
CREATE TABLE server_status (
    server_id   INTEGER PRIMARY KEY REFERENCES servers(id),
    status      TEXT DEFAULT 'unknown',  -- 'online'|'offline'|'degraded'|'unknown'
    last_seen   DATETIME,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### gpu_metrics (히스토리)

실시간 데이터는 메모리(WebSocket broadcast)에서 처리.
이 테이블은 60초 간격 아카이브용.

```sql
CREATE TABLE gpu_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id       INTEGER NOT NULL REFERENCES servers(id),
    gpu_index       INTEGER NOT NULL,
    utilization     REAL,       -- %
    memory_used     INTEGER,    -- MB
    memory_total    INTEGER,    -- MB
    temperature     REAL,       -- °C
    power_draw      REAL,       -- W
    active_users    TEXT,       -- JSON 배열 ['user1', 'user2']
    collected_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_gpu_metrics_server_time ON gpu_metrics(server_id, collected_at);
```

7일 이상 데이터 자동 삭제 (백그라운드 태스크).

### notes

```sql
CREATE TABLE notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL REFERENCES servers(id),
    username    TEXT NOT NULL,    -- 메모 작성자 (계정 없음, 본인 확인용만)
    content     TEXT NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### slack_alert_log (스팸 방지용)

```sql
CREATE TABLE slack_alert_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER REFERENCES servers(id),
    event_type  TEXT NOT NULL,    -- 'offline'|'recovery'|'degraded'|'gpu_full'
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 운영

### 백업

```bash
# 단순 파일 복사
cp data/gpu_monitor.db data/gpu_monitor_$(date +%Y%m%d).db
```

### 용량 추정

- 서버 10대, GPU 40개, 60초 간격, 7일 보관
- 7일 × 24시간 × 60분 × 40개 = 403,200 rows
- 행당 ~100 bytes = **약 40MB** → 문제없음

### WAL 모드 활성화

```python
# database.py
engine = create_engine("sqlite:///data/gpu_monitor.db")
with engine.connect() as conn:
    conn.execute(text("PRAGMA journal_mode=WAL"))
    conn.execute(text("PRAGMA synchronous=NORMAL"))
```
