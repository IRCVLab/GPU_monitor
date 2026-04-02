# Slack Takeover 계획

## 상황

- 기존 `gpu-monitor-backend` Docker 컨테이너가 Slack `Socket Mode`를 통해 `/gpu`를 처리하고 있다.
- 현재 `monitoring_v2`는 채널 로그용 `chat.postMessage` 경로와 HTTP `POST /slack/gpu` endpoint는 있지만, 런타임에 Slack 설정이 없다.
- 기존 환경에는 다음 키가 있다.
  - `SLACK_BOT_TOKEN`
  - `SLACK_APP_TOKEN`
  - `SLACK_LOG_CHANNEL_ID`
- 기존 환경에는 `SLACK_SIGNING_SECRET`가 없다.

## 결정

### 1. 즉시 전환 방식

기존 Slack App을 그대로 takeover 하려면 `monitoring_v2`도 `Socket Mode`를 지원해야 한다.

이유:

- 기존 배포는 HTTP slash command가 아니라 Socket Mode로 `/gpu`를 받고 있다.
- `SLACK_SIGNING_SECRET`가 없으므로 바로 HTTP slash command로만 전환하면 운영이 끊긴다.
- `SLACK_APP_TOKEN`은 이미 있으므로 Socket Mode takeover가 가장 짧고 안정적이다.

### 2. HTTP slash endpoint의 위치

- `POST /slack/gpu`는 유지한다.
- 다만 운영 takeover의 1차 경로는 아니다.
- 나중에 Slack App을 HTTP mode로 재설정할 때 사용할 수 있게 유지한다.

### 3. 24시간 히트맵 우선순위

- `24h heatmap`은 의미는 있다.
- 다만 Slack command/channel log takeover보다 우선순위가 낮다.
- Slack에서는 dense heatmap보다:
  - offline/degraded 서버
  - full GPU 서버
  - 최근 복구 서버
  - 최근 24시간 busiest server summary
  가 더 실용적이다.
- 따라서 Phase 4의 다음 순서는 `slash parity -> 간단 summary/statistics -> heatmap` 이다.

## 역할 분담

- `code-reviewer`
  - old/new Slack 경로 동시 실행 시 중복 알림 위험 점검
  - cutover 순서 검토
- `backend`
  - `monitoring_v2`에 Socket Mode command consumer 추가
  - 기존 channel log와 같은 token/config 재사용
- `fullstack`
  - old `/gpu` 사용 습관과 현재 `/slack/gpu` 기능 차이 정리
  - server query / network filter parity 보완
- `ui-designer`
  - Slack 명령 응답의 요약 block / 문제 서버 우선 배치 / noise 축소

## 구현 순서

1. Slack 전환용 공통 formatter 작성
2. HTTP `/slack/gpu`와 Socket Mode `/gpu`가 같은 formatter를 공유하도록 정리
3. `monitoring_v2` startup에 optional Slack Socket Mode runner 추가
4. 기존 env 키와 호환되도록 config 보강
5. `gpu-monitor-backend` 중지
6. `monitoring_v2`만 Slack command + channel log를 담당하도록 검증

## 검증 항목

- old Docker backend 중지 후 중복 알림이 사라지는지
- `monitoring_v2`가 `/gpu`, `/status`를 처리하는지
- `internal / external / all / offline / degraded / <server-query>` 필터가 동작하는지
- channel log가 기존 channel ID로 정상 전송되는지
- raw exception이 Slack에 노출되지 않는지
