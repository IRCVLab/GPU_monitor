# 데이터 수집 방식

## 결정: SSH Pull (모든 서버)

에이전트 설치 불필요. 모니터링 서버에서 아웃바운드 SSH로 직접 수집.

```
모니터링 서버 → SSH → 내부망 서버 (password or key)
모니터링 서버 → SSH → 외부망 서버 (AWS 등, SSH key)
```

**에이전트 push 방식을 선택하지 않은 이유:**
- 서버 등록 시 에이전트 파일 복사 + 실행 필요 → 마찰 큼
- SSH pull은 접속 정보만 입력하면 즉시 등록 가능
- 외부망 서버(AWS)도 모니터링 서버에서 outbound SSH 가능

---

## 수집 항목 및 주기

| 항목 | 수집 방법 | 주기 |
|------|---------|------|
| GPU utilization, memory, temp, power | `gpustat --json` | 10초 |
| GPU 프로세스 (pid → username 매핑) | `gpustat --json` 포함 | 10초 |
| CPU usage, RAM | `/proc/stat`, `/proc/meminfo` | 10초 |
| 디스크 사용량 | `/proc/mounts` + `statvfs` | 10분 (캐시) |
| 서버 상태 (온라인 여부) | SSH 연결 성공 여부 | 10초 |

---

## SSH 연결 관리

### 연결 유지 전략

```python
# 서버당 하나의 persistent SSH connection
# keepalive 30초 간격
# 1시간마다 강제 재연결 (메모리 누수 방지)
# 실패 시 15초 후 재시도
```

### 인증 방식

```
내부망 서버:
  - username + password (일반적)
  - username + SSH private key (선택)

외부망 서버 (AWS 등):
  - username + SSH private key (보통 ubuntu or ec2-user)
  - private key는 DB에 암호화 저장
```

### SSH key 보안

- 저장: SQLite, AES-256 암호화 (Fernet)
- 복호화: 메모리에서만, 디스크에 평문 저장 안 함
- 암호화 키: 환경변수 (`SECRET_KEY`)

---

## 오류 처리

| 상황 | 서버 상태 | 동작 |
|------|---------|------|
| SSH 연결 성공, gpustat 정상 | `online` | 정상 수집 |
| SSH 연결 성공, gpustat 실패 | `degraded` | CPU/RAM만 표시 |
| SSH 연결 실패 (타임아웃 등) | `offline` | 마지막 데이터 유지, 경과 시간 표시 |
| 등록된 서버가 처음 연결 안 될 때 | `unknown` | 재시도 대기 |

오프라인 → 온라인 / 온라인 → 오프라인 전환 시 Slack 채널에 알림.

---

## 수집 루프 구조

```python
# 서버별 독립 스레드 (또는 asyncio task)
async def collect_loop(server):
    while True:
        try:
            data = await collect(server.ssh_client)
            broadcast_websocket(server.id, data)
            if should_archive():  # 60초마다
                save_to_db(data)
        except SSHError:
            mark_offline(server)
            await asyncio.sleep(15)
            await reconnect(server)
        await asyncio.sleep(10)
```

각 서버 수집은 독립적. 하나 실패해도 다른 서버에 영향 없음.

---

## 히스토리 저장

- **60초마다** SQLite에 스냅샷 저장
- 보존 기간: **7일** (자동 삭제)
- 저장 항목: 서버별 평균 GPU utilization, 최대 memory, 활성 사용자 수
- 목적: 24시간 히트맵, 간단 통계

---

## 서버 등록 플로우

1. 사용자가 UI에서 SSH 정보 입력
2. 백엔드에서 즉시 연결 테스트 (10초 타임아웃)
3. 성공 → DB 저장, 수집 루프에 추가
4. 실패 → 오류 메시지 반환 (연결 불가 / 인증 실패 구분)
