# Slack 연동 설계

## 방향

- **채널 알림**: 서버 상태 변화를 Slack 채널에 로그
- DM 알림 없음 (채널 로그만)
- 이번 우선순위는 **채널 로그 UX 정리**이며, `/gpu` slash command는 별도 트랙으로 유지
- 기존 운영 Slack App takeover는 우선 `Socket Mode`로 처리하고, HTTP slash endpoint는 이후 전환 경로로 유지

---

## 채널 알림

### 알림 보낼 이벤트

| 이벤트 | 심각도 | 예시 |
|--------|--------|------|
| 서버 오프라인 | 🔴 CRITICAL | `Poseidon 오프라인 (10:32)` |
| 서버 복구 | 🟢 INFO | `Poseidon 복구됨 (10:47, 15분 다운)` |
| 서버 성능 저하 (degraded) | 🟡 WARNING | `Turing gpustat 오류, CPU/RAM만 수집 중` |
| 연결 경고 심화 | 🔴 CRITICAL | `Poseidon connection alert (180s)` |

### 스팸 방지 전략

**문제**: 서버가 불안정하면 on/off 알림이 도배됨.

**해결책:**
- 오프라인 알림: 연속 3번 실패 (15초) 후에만 전송 → 일시적 네트워크 끊김 무시
- 같은 이벤트 쿨다운: 10분 이내 동일 서버 동일 이벤트 재전송 안 함
- 복구 알림: 다운타임 5분 이상일 때만 전송 (짧은 재시작은 무시)
- `connection_warning` 는 DB 로그만 남기고 Slack 채널 전송은 안 함
- `connection_alert` 는 critical channel log 로 전송

### 메시지 포맷

검색성과 가독성을 위해 **자유문장보다 고정 토큰 + key=value** 구조를 쓴다.

### 텍스트 fallback

```text
[GPU][CRITICAL] Poseidon offline
server=poseidon host=166.104.167.11:2203 network=internal event=server_offline
server_id=1 detected_at=2026-03-22 22:10:15 KST last_seen=2026-03-22 22:09:40 KST reason=auth_failed
```

### Block Kit

- 첫 줄: 사람이 훑는 사건 요약
  - `*[GPU][CRITICAL]* Poseidon offline`
- 둘째 줄: 검색용 context
  - `server=poseidon · host=166.104.167.11:2203 · network=internal · event=server_offline`
- 셋째 줄: 부가 detail
  - `server_id=... · detected_at=... · last_seen=...`
  - recovery는 `downtime=...`
  - degraded/offline/alert는 `reason=... · source=...`

### 원칙

- 첫 줄 구조를 이벤트별로 고정한다
  - `Poseidon offline`
  - `Poseidon recovered`
  - `Poseidon degraded`
  - `Poseidon connection alert`
- 검색 키워드는 본문에 그대로 남긴다
  - `server=...`
  - `server_id=...`
  - `event=...`
  - `network=...`
- 절대 시각만 사용한다
- 원문 예외를 그대로 보내지 않고 정규화된 짧은 reason만 보낸다

---

## /gpu Slash Command

현재 endpoint는 유지하되, 운영 takeover의 1차 경로는 `Socket Mode`다.

- 이유: 기존 환경에 `SLACK_APP_TOKEN`은 있지만 `SLACK_SIGNING_SECRET`는 없다.
- 따라서 현재 `monitoring_v2`는:
  - `Socket Mode`로 `/gpu`, `/status` 처리
  - `POST /slack/gpu`는 future HTTP mode 전환용으로 유지

### 응답 형태 (Slack Block Kit)

```
/gpu 입력 시:

GPU 현황 (업데이트: 10:35:02)

● Poseidon                    ● Hinton
  GPU0: ████░░ 78% | 18/24GB    GPU0: ██░░░░ 24% | 6/24GB
  GPU1: ████░░ 40% | 8/24GB     GPU1: ████████ 82% | 20/24GB

✕ Turing  오프라인 (15분)     ● Lecun   정상
```

- Slack의 코드블록 or Block Kit 텍스트로 표현
- 내부망 서버만 기본 표시 (외부망은 `/gpu external` 로 따로)
- 응답은 ephemeral (요청한 사람만 보임) or 채널 공유 선택 가능

### 등록 방법

1. Slack App 생성 → Slash Commands 등록 (`/gpu`)
2. Request URL: `https://<모니터링서버>/slack/gpu`
3. 환경변수에 `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN` 설정

---

## 설정

```env
SLACK_BOT_TOKEN=xoxb-...         # Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...         # Socket Mode App Token
SLACK_SIGNING_SECRET=...          # Slash command 서명 검증
SLACK_LOG_CHANNEL=#gpu-monitor    # 알림 보낼 채널 이름 또는 ID
```

Slack 미설정 시 (`SLACK_BOT_TOKEN` 없으면) 알림 기능 비활성화. 시스템은 정상 작동.

---

## 구현 방식

- 채널 로그 전송은 `httpx + chat.postMessage`
- Slack log formatter를 하나로 모아서 event별 문구를 통일
- Slash command formatter는 HTTP route와 Socket Mode가 공유
- 운영 command takeover는 `slack-bolt + SocketModeHandler`
- Slash command endpoint는 `POST /slack/gpu` 유지
- 서명 검증은 slash command 경로에만 적용
