# Monitoring v2 Follow-ups

작성일: 2026-03-22

검토 범위:
- 대시보드 전역 갱신 루프와 WebSocket 경계
- 서버 카드, 메모 인증, 정렬 저장 계약
- 로그 페이지 시간 규칙, fetch race, 행 계층
- light mode surface 구조와 테스트 공백

## P0 Correctness

### 1. 메모 인증이 대상 서버에 묶여 있지 않다

- 근거:
  - [backend/routers/notes.py](/home/ircv/workspace/monitoring_v2/backend/routers/notes.py#L67)
  - [backend/routers/notes.py](/home/ircv/workspace/monitoring_v2/backend/routers/notes.py#L99)
  - [backend/routers/notes.py](/home/ircv/workspace/monitoring_v2/backend/routers/notes.py#L117)
- 문제:
  - 등록된 아무 서버에서나 SSH 로그인만 되면 다른 서버 메모를 생성/삭제할 수 있는 구조가 되기 쉽다.
  - 같은 username이 여러 서버에 있는 환경에서는 권한 경계가 틀어진다.
- 수정 방향:
  - note create/delete 모두 `target server` 기준으로만 SSH 인증한다.
  - 관리자 비밀번호 우회만 예외로 유지한다.

### 2. WebSocket이 메시지를 받고도 실제 상태 갱신에는 쓰이지 않는다

- 근거:
  - [frontend/src/lib/ws.ts](/home/ircv/workspace/monitoring_v2/frontend/src/lib/ws.ts#L31)
  - [frontend/src/routes/+layout.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/+layout.svelte#L1)
  - [backend/collectors/server_collector.py](/home/ircv/workspace/monitoring_v2/backend/collectors/server_collector.py#L370)
- 문제:
  - 백엔드는 계속 broadcast하지만 프론트는 payload를 버린다.
  - 헤더의 `Live` 계열 표기와 실제 데이터 ownership이 어긋난다.
- 수정 방향:
  - websocket을 연결 상태 전용으로 쓸지, 서버 상태 merge path로 살릴지 결정한다.
  - 어느 쪽이든 UI 문구는 실제 계약과 일치해야 한다.

### 3. 헤더의 갱신 상태가 실제 데이터 신선도와 정확히 대응하지 않는다

- 근거:
  - [frontend/src/routes/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/+page.svelte#L155)
  - [frontend/src/routes/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/+page.svelte#L271)
  - [frontend/src/routes/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/+page.svelte#L396)
- 문제:
  - status-only refresh도 `마지막 전체 갱신`처럼 읽힐 수 있다.
  - socket 연결 여부가 신선도 판단에 섞여 있다.
- 수정 방향:
  - `전체 카탈로그 갱신`, `상태 스냅샷 갱신`, `지연/실패`를 분리해서 표기한다.
  - freshness는 성공한 데이터 갱신 시각 기준으로만 계산한다.

## P1 Refresh / Ordering

### 4. `n초 뒤 자동 갱신`은 전역 서버 동기 갱신 타이밍이 아니다

- 근거:
  - [frontend/src/routes/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/+page.svelte#L186)
  - [backend/collectors/server_collector.py](/home/ircv/workspace/monitoring_v2/backend/collectors/server_collector.py#L184)
- 문제:
  - 프론트 카운트다운은 페이지 기준 polling timer일 뿐, 서버 전체가 한 번에 갱신되는 시점과 다르다.
- 수정 방향:
  - 문구를 `다음 상태 확인 예정`처럼 축소하거나 제거한다.
  - 전역 staleness 모델이 없으면 전역 카운트다운을 약속하지 않는다.

### 5. 정렬 source of truth가 쿠키와 `display_order`로 이원화돼 있다

- 근거:
  - [frontend/src/lib/stores/order.ts](/home/ircv/workspace/monitoring_v2/frontend/src/lib/stores/order.ts#L1)
  - [backend/routers/servers.py](/home/ircv/workspace/monitoring_v2/backend/routers/servers.py#L507)
  - [backend/collectors/manager.py](/home/ircv/workspace/monitoring_v2/backend/collectors/manager.py#L20)
- 문제:
  - 브라우저별 쿠키 순서와 공용 `display_order`가 쉽게 갈라진다.
  - 쿠키 유실 시 순서가 다시 바뀌고, 관리자 기대와도 어긋난다.
- 수정 방향:
  - 공용 순서와 개인 순서를 분리하거나 하나만 공식 계약으로 남긴다.
  - 최소한 현재 계약은 별도 md로 명시한다.

### 6. full refresh와 status-only refresh는 분리됐지만 문서화와 표시가 아직 부족하다

- 근거:
  - [frontend/src/routes/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/+page.svelte#L84)
  - [frontend/src/routes/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/+page.svelte#L146)
- 문제:
  - 구현은 분리되기 시작했지만, 어떤 작업이 catalog refresh를 강제하는지 아직 문서와 UI가 약하다.
- 수정 방향:
  - 관리 작업 직후만 full refresh를 쓰고, 주기 루프는 status-only로 고정한다.
  - 헤더 문구도 그 차이를 반영한다.

## P2 Logs

### 7. 로그 목록 fetch는 race에 취약하다

- 근거:
  - [frontend/src/routes/logs/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/logs/+page.svelte#L58)
  - [frontend/src/routes/logs/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/logs/+page.svelte#L139)
- 문제:
  - 빠른 필터 변경이나 load more 중첩 시 느린 응답이 최신 결과를 덮을 수 있다.
- 수정 방향:
  - request version 또는 abort controller로 stale response를 무시한다.
  - filter reset 시 expanded row와 pagination state도 함께 초기화한다.

### 8. 로그 헤더와 행 계층은 아직 Apple-like compact hierarchy 기준에 못 미친다

- 근거:
  - [frontend/src/routes/logs/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/logs/+page.svelte#L168)
  - [frontend/src/routes/logs/+page.svelte](/home/ircv/workspace/monitoring_v2/frontend/src/routes/logs/+page.svelte#L332)
- 문제:
  - `최근 갱신`, `실패`, `loading` 맥락이 약하고, 행 확장 affordance도 명확하지 않다.
- 수정 방향:
  - 헤더에 fetch lifecycle을 보이고, 행은 `severity / content / absolute time` 3-zone으로 재정리한다.

## P2 Design System

### 9. light mode가 전역 substring selector와 `!important`에 과도하게 의존한다

- 근거:
  - [frontend/src/app.css](/home/ircv/workspace/monitoring_v2/frontend/src/app.css#L50)
  - [frontend/src/app.css](/home/ircv/workspace/monitoring_v2/frontend/src/app.css#L390)
- 문제:
  - 최근의 타이틀, idle text, 보조 메타 대비 이슈가 다시 재발하기 쉬운 구조다.
- 수정 방향:
  - `page / header / card / overlay` surface tier와 `primary / secondary / tertiary` text tier를 토큰화한다.
  - 전역 substring override는 단계적으로 걷어낸다.

### 10. compact 디자인 방향은 맞지만 refresh, memo, system 블록의 UX 계약이 아직 흐리다

- 출처:
  - `ui-designer` 검토 결과 요약
- 수정 방향:
  - 헤더는 one-line status summary 중심으로 정리한다.
  - 메모는 존재할 때만 드러나고, 접힘 상태와 펼침 상태가 실제로 달라야 한다.
  - system GPU spec은 dense mini-grid로 두되, primary GPU rows를 침범하지 않게 한다.

## P3 Tests

### 11. 핵심 회귀 경로에 대한 자동화 테스트가 없다

- 근거:
  - `rg --files /home/ircv/workspace/monitoring_v2 | rg "test|spec"` 결과 없음
- 우선 추가 대상:
  - 대시보드 refresh scheduler와 stale-state 표시
  - websocket state merge 또는 connection-only semantics
  - 로그 filter/pagination race
  - note auth boundary
  - order persistence contract

## Agent 분배

- `code-reviewer`: 수정 후 남은 correctness risk와 테스트 공백 재검증
- `ui-designer`: surface tier, refresh semantics, log hierarchy 설계 문서화
- `frontend-developer`: websocket ownership, 헤더 semantics, 로그 계층 정리
- `fullstack-developer`: order contract 문서화와 migration path 제안
- `backend-developer`: note auth boundary, reorder/display_order 계약 검토

## 권장 실행 순서

1. note auth boundary와 websocket ownership 정리
2. refresh semantics를 문구와 구현 모두에서 일치시킴
3. order source of truth를 md로 확정
4. logs race와 hierarchy 정리
5. token 기반 light-mode 정리와 최소 테스트 추가
