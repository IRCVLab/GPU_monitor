# 로그 설계 규칙 및 구현 계획

> 작성일: 2026-03-22
> 목적: 이벤트 로그의 의미, 표시 우선순위, 시간 처리, 집계 단위를 먼저 고정한 뒤 구현한다.

---

## 1. 현재 문제

- `info` 로그가 거의 무채색이라 `warning`/`critical`과 정보 계층이 약하다.
- 같은 유저가 GPU 4개를 동시에 점유하면 `process_start` / `process_end`가 GPU별로 중복 기록된다.
- 로그 저장 시각이 UTC naive 값으로 보이고, 프론트는 이를 사용자 로컬 시각으로 해석해 KST 기준으로 약 9시간 어긋나 보일 수 있다.
- 로그 페이지는 severity 색, 시간 파싱, 절대시간 표시 규칙이 문서화돼 있지 않다.

---

## 2. Canonical Event Contract

로그 의미는 프론트가 아니라 백엔드가 결정한다.
프론트는 canonical event를 그대로 렌더링만 한다.

### 필드 규칙

- `event_type`: 이벤트의 기계적 분류
- `severity`: `critical | warning | info`
- `message`: 사람이 바로 읽는 한 줄 요약
- `metadata`: UI/디버깅/후속 연동용 구조화 데이터
- `created_at`: **UTC timezone-aware ISO 8601** 문자열

### severity 의미

- `critical`
  - 서비스 사용에 즉시 영향
  - 예: `server_offline`, `connection_alert`
- `warning`
  - 즉시 장애는 아니지만 운영자가 봐야 함
  - 예: `server_degraded`, `connection_warning`
- `info`
  - 상태 변화/활동 정보
  - 예: `server_online`, `process_start`, `process_end`

---

## 3. Event Aggregation Rules

### 원칙

- DB에는 가능한 한 **서버 단위 canonical event**만 남긴다.
- 프론트에서 중복 로그를 숨기거나 합치지 않는다.
- 같은 polling cycle 안에서 발생한 동일 사용자 이벤트는 GPU별 raw row로 쪼개지지 않게 한다.

### `process_start` / `process_end`

기존 문제:
- 같은 사용자가 GPU 0,1,2,3에 동시에 올라가면 로그가 4개 생긴다.

새 규칙:
- **서버 + 사용자 + 이벤트 종류 + 단일 수집 주기** 기준으로 1개 이벤트만 기록한다.
- `metadata`에는 GPU 배열을 담는다.

예시:

```json
{
  "event_type": "process_start",
  "severity": "info",
  "message": "alice started on GPUs 0,1,2,3",
  "metadata": {
    "user": "alice",
    "gpu_indices": [0, 1, 2, 3],
    "gpu_count": 4
  }
}
```

동일 규칙을 `process_end`에도 적용한다.

### 비집계 이벤트

- `server_offline`
- `server_online`
- `server_degraded`
- `connection_warning`
- `connection_alert`

위 이벤트는 서버 상태 전환이므로 기존처럼 한 번씩 기록한다.

---

## 4. Time Contract

시간은 저장과 표시를 분리한다.

### 저장 규칙

- 백엔드 내부 canonical time은 항상 UTC
- DB `created_at`은 timezone-aware UTC datetime 사용
- API 직렬화 시 `Z` 또는 `+00:00`이 포함된 ISO 8601 문자열 반환

금지:

- timezone 없는 naive UTC 문자열을 프론트로 그대로 보내기

### 프론트 표시 규칙

- 절대시간: 사용자 로컬 기준
- 로그 목록과 상세 모두 절대시간을 기본으로 보여준다
- 대시보드 상태 요약처럼 실시간성만 중요한 UI에만 상대시간을 제한적으로 쓴다
- 브라우저 timezone이 KST면 KST로, 다른 timezone이면 해당 로컬 timezone으로 보여준다

### 로그 시간 규칙

- 로그 페이지에서는 `N초 전`, `N분 전`, `N시간 전`을 쓰지 않는다
- 로그 행 우측 시간과 펼침 상세 시간은 같은 절대시각 기준이어야 한다
- 시간 표기는 초 단위까지 유지한다

예시:

- 행 우측: `2026.03.22 14:03:12 KST`
- 상세: `2026.03.22 14:03:12 KST`

---

## 5. Log UI Rules

### severity 색상

- `critical`: red
  - 가장 강한 보더/배경/텍스트 대비
- `warning`: amber
  - critical보다 한 단계 낮은 강도
- `info`: blue
  - 회색이 아니라 “정상 정보”로 인식되는 낮은 채도의 blue

### row 계층

- 배지만 색이 있는 구조는 부족하다
- severity에 따라 row left border 또는 subtle tint를 추가한다
- expanded 상태에서도 severity 계층이 유지돼야 한다

### 정보 우선순위

한 행의 정보 순서는 아래를 따른다.

1. severity
2. server_name
3. message
4. event_type
5. relative time

expanded 시 아래를 추가한다.

- absolute local time
- metadata JSON 또는 구조화 정보

---

## 6. 구현 순서

1. 백엔드 시간 저장/직렬화 규칙을 UTC aware로 고정
2. `process_start` / `process_end`를 서버 단위 집계 이벤트로 변경
3. 로그 API 응답의 `created_at`과 `metadata` 계약을 재확인
4. 프론트 로그 페이지에서 UTC-safe parsing + local timezone formatting 적용
5. severity badge/row 색 계층 조정
6. 샘플 로그 기준으로 절대시간/KST 표시를 검증

---

## 7. 수정 대상 파일

백엔드:

- `backend/models.py`
- `backend/event_logger.py`
- `backend/collectors/server_collector.py`
- `backend/routers/logs.py`

프론트:

- `frontend/src/routes/logs/+page.svelte`
- `frontend/src/app.css`
- 필요 시 `frontend/src/lib/types.ts`

---

## 8. 검증 기준

- 같은 사용자가 GPU 4개를 동시에 쓰는 경우 로그는 1개만 생성된다.
- 새로 생성된 로그가 KST 사용자에게 `9시간 전`처럼 잘못 보이지 않는다.
- `info`가 blue 계열로 보여서 `warning`/`critical`과 계층이 분명하다.
- relative time과 expanded absolute time이 서로 일치한다.
