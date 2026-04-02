# Server Order Contract

작성일: 2026-03-22

## 현재 상태

- 프론트는 쿠키 `serverOrder`를 읽고 저장한다.
  - 구현: `frontend/src/lib/stores/order.ts`
- 대시보드 렌더링은 쿠키 순서를 우선 적용하고, 쿠키에 없는 서버만 `display_order`로 뒤에 붙인다.
  - 구현: `frontend/src/routes/+page.svelte`
- 백엔드는 `servers.display_order`를 계속 반환하고, `PUT /servers/reorder`도 유지한다.
  - 구현: `backend/routers/servers.py`

즉 현재는:
- 개인별 쿠키 순서
- 서버 공용 `display_order`

두 source of truth가 동시에 존재한다.

## 결정

`monitoring_v2`의 공식 정렬 source of truth는 **프론트 쿠키 기반 개인 정렬**로 둔다.

`display_order`는 당분간 **기본 시드 순서 / fallback 순서**로만 사용한다.

## 이유

- 이 프로젝트는 계정 시스템이 없고 공개 대시보드 성격이 강하다.
- 사용자별로 보고 싶은 서버 순서가 다를 가능성이 높다.
- 공용 순서를 쓰려면 관리자 권한, 저장 시점, 다중 사용자 충돌 정책까지 같이 닫아야 한다.
- 현재 코드도 이미 쿠키 overlay 쪽으로 기울어 있다.

## tradeoff

장점:
- 구현이 가장 단순하다.
- 인증/권한 모델을 늘리지 않아도 된다.
- 사용자는 브라우저별로 바로 자기 순서를 가질 수 있다.

단점:
- 브라우저/기기 간 순서가 공유되지 않는다.
- 운영자가 “모두에게 같은 카드 순서”를 강제할 수 없다.
- `display_order`와 쿠키가 함께 남아 있으면 코드 이해가 혼란스러울 수 있다.

## 계약 규칙

1. 대시보드 실제 표시 순서는 쿠키 `serverOrder`를 우선한다.
2. 쿠키에 없는 서버는 API가 내려준 `display_order`, 그다음 `server_id` 순으로 뒤에 붙인다.
3. drag reorder는 쿠키만 갱신한다.
4. `PUT /servers/reorder`는 MVP 경로에서 사용하지 않는다.
5. `display_order`는 신규 서버가 처음 보일 때의 기본 위치를 정하는 fallback 필드로 유지한다.

## 최소 마이그레이션 경로

### Phase 1

- 이 문서를 기준 계약으로 채택한다.
- 프론트 주석/리뷰 문서에서 “공용 정렬” 가정을 제거한다.
- reorder 저장 UI는 쿠키만 갱신하도록 유지한다.

### Phase 2

- 백엔드 `PUT /servers/reorder`를 더 이상 호출하지 않도록 정리한다.
- `display_order`는 서버 생성 시 초기 정렬 seed 용도로만 남긴다.
- 관리자 UI 문구에도 “이 브라우저에서만 순서가 저장됨”을 명시한다.

### Phase 3

- 미래에 계정 시스템이 생기면:
  - 쿠키 기반 정렬을 사용자 프로필 저장소로 승격
  - 그때 `display_order`를 제거하거나 “관리자 기본 순서”로 재정의

## 구현 체크리스트

- 프론트:
  - `serverOrder` 쿠키가 1차 source라는 주석/문서 정리
  - reorder UX 문구에 개인 저장 의미 반영
- 백엔드:
  - `PUT /servers/reorder`는 당장 삭제하지 말고 미사용 경로로 둠
  - `display_order`는 list fallback 정렬용으로만 유지
- 문서:
  - 계획/리뷰 문서에서 order ownership을 이 결정으로 통일

## 한 줄 결론

MVP에서는 **쿠키 기반 개인 정렬**로 닫고, `display_order`는 **초기 fallback 순서**로만 남긴다.
