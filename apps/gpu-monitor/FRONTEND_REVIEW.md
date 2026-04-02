# 프론트엔드 코드 리뷰

기준:
- 이 문서는 현재 시점의 미해결 이슈만 유지한다.
- 해결된 항목이나 과거 수정 이력은 남기지 않는다.

검토 범위:
- `frontend/src/routes/*.svelte`
- `frontend/src/lib/**/*.ts`
- `frontend/src/lib/components/*.svelte`
- `frontend/vite.config.ts`

검증:
- 기능 문서 확인: `PLAN.md`, `feature/*.md`
- 정적 확인: `npm run check`
- 빌드 확인: `npm run build`
- 테스트 탐색: 저장소 내 브라우저/E2E 테스트 파일 없음

최근 코드 검토: 2026-03-21

## Open Findings

### 1. 페이지 초기화가 여전히 모듈 레벨 브라우저 부트스트랩과 전역 cleanup sentinel에 의존한다

관련 파일:
- `frontend/src/routes/+layout.svelte:7`
- `frontend/src/routes/+page.svelte:125`

문제:
- `+layout.svelte`, `+page.svelte` 모두 `onMount` 대신 모듈 로드 시점에 함수를 직접 실행한다.
- 정리 로직도 컴포넌트 lifecycle이 아니라 `globalThis.__monitoringV2*Cleanup` 슬롯에 저장된다.

영향:
- HMR, 라우트 재마운트, 초기화 순서 변경에 민감하다.
- 이번에 해결한 "불러오는 중..." 회귀와 유사한 문제가 다시 들어오더라도 원인 추적이 어렵다.

권고:
- 지금 구조를 또 급하게 건드리기보다, 먼저 브라우저 스모크 테스트를 만든 뒤 lifecycle 기반으로 정리하는 편이 안전하다.

코드 검토 결과 (2026-03-21):
- `+layout.svelte:32`에서 `initLayoutRuntime()`이 스크립트 최상위에서 직접 호출됨. `onMount` 미사용.
- `+page.svelte:157`에서 `initPageRuntime()`이 동일하게 직접 호출됨.
- 두 파일 모두 `globalThis.__monitoringV2LayoutCleanup` / `__monitoringV2PageCleanup` sentinel 패턴 유지 중.
- 문제 그대로 존재. 수정 방향: `initLayoutRuntime()` / `initPageRuntime()` 호출을 `onMount` 콜백 내로 이동하고, `onDestroy`에서 cleanup 호출 → globalThis sentinel 제거 가능.

### 2. 서버 순서 저장 로직이 백엔드 `display_order`와 분리돼 있어 공용 정렬이 깨진다

관련 파일:
- `frontend/src/lib/stores/servers.ts:73`
- `frontend/src/lib/stores/order.ts`
- `frontend/src/routes/+page.svelte:173`
- `backend/routers/servers.py:153`
- `backend/routers/servers.py:189`

문제:
- 백엔드는 `display_order`와 `/servers/reorder`를 제공하지만 프론트는 이를 호출하지 않는다.
- `internalServers`, `externalServers`는 현재 상태를 다시 `server_id` 기준으로 정렬해 버린다.
- 현재 drag 결과는 브라우저 `localStorage`에만 저장된다.

영향:
- 다른 브라우저/사용자/장치에서는 순서가 공유되지 않는다.
- 새 서버가 추가되거나 상태가 다시 로드될 때 기대한 정렬이 쉽게 무너진다.

권고:
- 정렬의 source of truth를 하나로 정해야 한다.
- 공용 순서를 쓸 거면 `display_order`를 상태 모델에 포함하고 drag 결과를 `/servers/reorder`로 저장해야 한다.

코드 검토 결과 (2026-03-21):
- `order.ts:22-27` - `saveOrder()`는 `localStorage.setItem` + `serverOrder.set()` 만 실행. `/api/servers/reorder` 호출 없음.
- `+page.svelte:213` / `220` - `drop()`, `dragEnd()` 모두 `saveOrder()` 만 호출.
- `servers.ts:73-83` - `internalServers`, `externalServers` derived store가 `.sort((a,b) => a.server_id - b.server_id)` 로 정렬하여 API의 `display_order` 값을 완전히 무시.
- `GET /servers`는 `order_by(Server.display_order, Server.id)` 로 반환하고, `PUT /servers/reorder`도 존재하나 프론트에서 둘 다 미사용.
- 문제 그대로 존재. 수정 방향: `ServerState` 타입에 `display_order` 필드 추가 → `normalizeServerState`에서 파싱 → derived store sort를 `display_order` 기준으로 변경 → `saveOrder`를 `/api/servers/reorder` 호출로 교체.

### 3. 서버 수정/삭제 기능이 구현돼 있어도 대시보드에서는 진입할 수 없다

관련 파일:
- `frontend/src/routes/+page.svelte:243`
- `frontend/src/routes/+page.svelte:364`
- `frontend/src/lib/components/ServerForm.svelte:120`

문제:
- 페이지에서는 `ServerForm`을 항상 "등록" 용도로만 열고, `editServer`를 전달하는 경로가 없다.
- `ServerForm` 내부에는 수정/삭제 로직이 있지만 실제 UI에서 그 상태로 진입할 수 없다.

영향:
- 코드상 존재하는 관리 기능이 사용자 입장에서는 사실상 없는 기능이 된다.
- 유지보수 시 dead path가 늘어나고, 수정/삭제 플로우는 실제 사용 없이 깨지기 쉽다.

권고:
- 카드에서 편집 진입점을 제공하거나, 쓰지 않을 기능이면 아예 제거해 코드 경로를 줄이는 편이 낫다.

코드 검토 결과 (2026-03-21):
- `+page.svelte:364` - `<ServerForm bind:open={adminOpen} ... />` 에 `editServer` prop이 전달되지 않음. 항상 null(신규 등록 모드).
- `ServerCard.svelte` 전체에 편집 버튼이나 `onEdit` 콜백이 없음.
- `ServerForm.svelte:8`의 `export let editServer` prop과 31-57줄의 수정 모드 로직, 120-140줄의 삭제 로직은 완비돼 있으나 non-null 값이 들어올 경로가 없음.
- 문제 그대로 존재. 수정 방향: `+page.svelte`에 `let editingServer = $state<ServerRecord | null>(null)` 추가 → `ServerCard`에 `onEdit` 콜백 prop 추가 → 편집 버튼 클릭 시 `editingServer` 설정 → `ServerForm`에 `bind:editServer={editingServer}` 전달.

### 4. 서버 연결 테스트가 현재 폼 payload 기준이 아니라 저장된 서버 기준으로만 동작한다

관련 파일:
- `frontend/src/lib/components/ServerForm.svelte:67`
- `frontend/src/lib/components/ServerForm.svelte:103`
- `feature/collect.md:90`

문제:
- 신규 등록에서는 테스트 버튼이 아예 없다.
- 수정 화면의 테스트도 사용자가 방금 입력한 host/port/credential이 아니라 이미 저장된 서버 레코드를 검사한다.

영향:
- "저장 전에 확인"이라는 문서상 UX가 성립하지 않는다.
- 수정 중인 credential이 맞는지 저장 전에는 검증할 수 없다.

권고:
- 신규 등록과 수정 모두 현재 폼 payload 기준의 테스트 경로를 제공해야 한다.

코드 검토 결과 (2026-03-21):
- `ServerForm.svelte:103-105` - `handleTest()`가 `if (!editServer) return` 가드로 즉시 반환. 신규 모드에서 호출 불가.
- `ServerForm.svelte:304` - 템플릿에서도 `{#if editServer}` 블록 내에서만 테스트 버튼 렌더링.
- `ServerForm.svelte:111` - `testConnection(editServer.id, adminPassword)` — 현재 폼 입력값이 아닌 DB 저장 서버 ID 기준으로 백엔드 테스트.
- 문제 그대로 존재. 수정 방향: 백엔드에 `POST /servers/test-credentials` (id 없이 host/port/credentials payload) 추가 필요. 신규 등록 모드에서 해당 endpoint 호출. 수정 모드도 폼 값이 변경된 경우 동일 endpoint 사용.

### 5. 메모 UI는 조회 실패를 숨기고, 관리자 삭제 기능도 드러내지 않는다

관련 파일:
- `frontend/src/lib/components/ServerCard.svelte:42`
- `frontend/src/lib/components/ServerCard.svelte:61`
- `frontend/src/lib/api.ts:99`

문제:
- 메모 조회 실패는 catch에서 완전히 무시돼 사용자가 실패 여부를 알 수 없다.
- 삭제 API는 `admin_password`를 지원하지만 카드 UI에서는 작성자 비밀번호 입력만 가능하다.

영향:
- 메모가 "없는 것"과 "불러오지 못한 것"이 UI에서 구분되지 않는다.
- 문서상 가능한 관리자 삭제 기능을 프론트에서 사용할 수 없다.

권고:
- 조회 실패 메시지와 재시도 동선을 보여줘야 한다.
- 관리자 삭제가 필요한 제품 요구라면 UI에도 대응 입력 경로를 열어야 한다.

코드 검토 결과 (2026-03-21):
- `ServerCard.svelte:44-53` - `toggleNotes()` catch 블록이 `// leave notes empty on error` 주석만 남기고 오류 상태 변수 없음. 오류 메시지를 담을 `notesError` 상태 자체가 존재하지 않음.
- `api.ts:99-114` - `deleteNote()`에 `adminPassword?: string` 5번째 optional 인자가 추가돼 있음 (이 부분은 이전 대비 개선).
- `ServerCard.svelte:66` - `deleteNote(server.server_id, note.id, note.username, pw)` 4인자 호출. `adminPassword` 인자가 전달되지 않아 api.ts의 admin 지원이 UI에서 사용 불가.
- `ServerCard.svelte:17-19` - `deletePassword` 상태만 있고, admin password를 별도로 받는 상태/입력 필드 없음.
- 조회 실패 숨김은 그대로, 관리자 삭제는 api.ts만 부분 개선됐으나 UI는 미연결. 수정 방향: `notesError = ''` 상태 추가 → catch에서 오류 문구 설정 → 재시도 버튼 렌더링 / 관리자 삭제는 카드에 admin_password 입력 필드 또는 관리자 모드 토글 추가 후 `handleDelete`에서 5번째 인자 전달.

## Testing Gap

### 1. 브라우저 수준 회귀 테스트가 없다

현재 특히 필요한 테스트:
- 첫 진입 후 스피너가 사라지고 서버 카드 또는 빈 상태 문구가 보이는지
- 라우트 재마운트/HMR 시 WebSocket 중복 연결이나 cleanup 누락이 없는지
- drag reorder 후 새로고침/다른 브라우저에서 순서가 어떻게 보존되는지
- 메모 조회 실패 시 오류가 사용자에게 보이는지

## Resolved

현재 이 섹션에 이동된 항목 없음.

## 결론

현재 프론트엔드의 핵심 리스크는 lifecycle을 우회하는 초기화 구조, 정렬 저장 구조의 이중화, 관리 UI 미노출, 그리고 메모 실패 상황의 fallback 부족이다.
2026-03-21 코드 검토 기준, 5개 Open Findings 모두 코드에 그대로 잔존함. Finding 5의 `api.ts`에서 `deleteNote`의 `adminPassword` optional 인자가 추가된 것이 유일한 부분 개선이나, 이를 사용하는 UI가 없어 사용자에게 노출되지 않는다.
