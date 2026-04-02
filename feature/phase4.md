# Phase 4 정리

## 범위

- Slack 채널 로그 정리
- Slack `/gpu` slash command 유지
- 24시간 히트맵
- 간단 통계 페이지

## 이번 라운드 우선순위

1. Slack 채널 로그를 운영용 로그처럼 정리한다.
2. DM은 만들지 않는다.
3. 메시지는 사람이 바로 읽히고, 검색도 쉬워야 한다.
4. 원문 예외는 숨기고 정규화된 reason만 노출한다.

## 역할 분담

- `ui-designer`
  - Slack 메시지의 시각적 계층 구조 정리
  - 요약 1줄, 검색용 context 1줄, detail 1줄 구조 유지
- `backend / fullstack`
  - collector 상태 변화와 Slack formatter 연결
  - offline / degraded / recovery / connection_alert 이벤트 정리
  - dedupe / cooldown / 절대 시각 규칙 유지
- `code-reviewer`
  - 검색 토큰 일관성
  - raw exception 노출 여부
  - Slack 도배 가능성 점검

## 현재 구현 기준

- 채널 전송: `httpx + chat.postMessage`
- fallback text + Block Kit 동시 사용
- 검색 토큰:
  - `server=...`
  - `server_id=...`
  - `host=...:port`
  - `network=...`
  - `event=...`
- 절대 시각:
  - `detected_at=...`
  - `last_seen=...`
  - `recovered_at=...`
- 부가 정보:
  - `downtime=...`
  - `elapsed=...`
  - `reason=...`
  - `source=...`

## 다음 작업

1. `/gpu` 응답에 운영 summary와 query parity를 더 다듬기
2. 24시간 히트맵은 web dashboard 통계 기능으로 후순위 설계
3. heatmap 전에 `top users / busiest servers / contention windows` 요약부터 검토
