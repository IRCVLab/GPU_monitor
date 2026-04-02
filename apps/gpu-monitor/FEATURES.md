# Feature Implementation Plan

> 작성일: 2026-03-21
> 기준 코드: current main branch

---

## 목차

1. [서버 삭제 UI](#1-서버-삭제-ui)
2. [수동 새로고침](#2-수동-새로고침)
3. [이벤트 로그 시스템](#3-이벤트-로그-시스템)
4. [GPU 카드 레이아웃 개선](#4-gpu-카드-레이아웃-개선)

---

## 1. 서버 삭제 UI

### 현황
- 백엔드 `DELETE /servers/{id}` 엔드포인트 **이미 존재** (admin_password 필요)
- `api.ts`에 `deleteServer(id, adminPassword)` 함수 **이미 존재**
- 프론트엔드 UI 없음

### 목표
- 헤더 "서버 등록" 버튼 옆에 "서버 삭제" 버튼 추가
- 관리자 비밀번호 입력 후 → 서버 목록 표시 → 선택 삭제
- 삭제 시 확인 다이얼로그 (실수 방지)

### 구현 세부사항

#### Frontend

**새 컴포넌트: `ServerDeleteModal.svelte`**
```
Props:
  open: boolean
  onClose: () => void
  onDeleted: () => void | Promise<void>

상태:
  step: 'auth' | 'select' | 'confirm'
  adminPassword: string
  servers: ServerRecord[]        ← GET /servers 조회
  selectedId: number | null
  selectedName: string
  deleting: boolean
  error: string

흐름:
  step='auth'   → 관리자 비밀번호 입력
  step='select' → 서버 목록 라디오/리스트 선택
  step='confirm'→ "서버명을 삭제합니다. 복구 불가" 확인
  → deleteServer(id, adminPassword) 호출
  → 성공 시 onDeleted() 호출 + 모달 닫기
```

**`+page.svelte` 변경**
- 헤더에 버튼 추가: `<button onclick={() => (deleteOpen = true)}>서버 삭제</button>`
- `deleteOpen` state 추가
- `<ServerDeleteModal bind:open={deleteOpen} onDeleted={handleSaved} />`

**단계별 비밀번호 검증**
- 실제 검증은 `deleteServer()` 호출 시 백엔드에서 수행
- `step='auth'` 단계에서 password가 비어있으면 다음 단계로 이동 불가
- 서버 목록은 관리자 비밀번호 없이 조회 가능 (GET /servers는 공개)

#### 변경 파일
- `frontend/src/lib/components/ServerDeleteModal.svelte` — **신규**
- `frontend/src/routes/+page.svelte` — 버튼 + state 추가
- `frontend/src/lib/api.ts` — deleteServer 이미 있음, 수정 불필요

---

## 2. 수동 새로고침

### 현황
- `reloadDashboard()` 함수 존재, `onMount` 시 1회 호출
- WebSocket으로 실시간 업데이트는 되지만 수동 트리거 UI 없음
- 자동 폴링 없음 (WS가 오프라인이면 데이터가 멈춤)

### 목표
- 헤더 우상단에 새로고침 버튼
- 클릭 중 스피너 애니메이션
- 최근 갱신 시각 표시 ("마지막 업데이트: N초 전") — 이미 있음

### 구현 세부사항

**`+page.svelte` 변경**
```svelte
let refreshing = $state(false);

async function handleRefresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    await reloadDashboard();
  } finally {
    refreshing = false;
  }
}
```

**버튼 UI**
```svelte
<button
  class="btn-ghost"
  onclick={handleRefresh}
  disabled={refreshing}
  aria-label="새로고침"
>
  <svg class="w-4 h-4 {refreshing ? 'animate-spin' : ''}"><!-- refresh icon --></svg>
</button>
```

#### 변경 파일
- `frontend/src/routes/+page.svelte` — 버튼 + refreshing state 추가

---

## 3. 이벤트 로그 시스템

### 설계 원칙: **가벼워야 함**
- 대시보드 초기 로드 시 로그 **미포함** — 로그 패널 열 때만 로드
- 기본 조회 최신 **50건** (페이지네이션)
- DB 인덱스: `(created_at DESC)`, `(server_id, created_at DESC)`
- 7일 초과 레코드 자동 정리 (기존 `archive_interval` 루프에 포함)
- WebSocket 브로드캐스트 없음 — 로그 패널 폴링 or 수동 새로고침

### 이벤트 종류

| event_type | severity | 조건 | Slack |
|---|---|---|---|
| `server_offline` | critical | SSH 연결 실패 3회 연속 | ✅ |
| `server_online` | info | 오프라인 → 온라인 복구 | ✅ |
| `server_degraded` | warning | SSH OK, GPU 수집 실패 | ✅ |
| `connection_warning` | warning | 마지막 수집 성공 후 **3분 미만** 응답 없음 | ❌ (DB만) |
| `connection_alert` | critical | 마지막 수집 성공 후 **3분 이상** 응답 없음 | ✅ |
| `process_start` | info | GPU users 배열에 새 유저 추가 감지 | ❌ |
| `process_end` | info | GPU users 배열에서 유저 제거 감지 | ❌ |

> **connection_warning vs server_offline 차이**
> - `server_offline`: SSH 자체가 안 됨
> - `connection_warning/alert`: SSH는 살아있는데 수집 데이터가 오래됨 (degraded 에서 시간 경과)

### Backend 구현

#### 3-1. DB 스키마 (`models.py`)

```python
class EventLog(Base):
    __tablename__ = "event_logs"

    id          = Column(Integer, primary_key=True)
    server_id   = Column(Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=True)
    server_name = Column(String, nullable=True)   # 서버 삭제 후에도 로그 보존
    event_type  = Column(String, nullable=False)  # 위 표 참조
    severity    = Column(String, nullable=False)  # info | warning | critical
    message     = Column(String, nullable=False)
    metadata    = Column(JSON, nullable=True)      # gpu_index, username, pid 등 부가 정보
    created_at  = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_event_logs_created_at", "created_at"),
        Index("ix_event_logs_server_created", "server_id", "created_at"),
    )
```

#### 3-2. 이벤트 기록 유틸 (`event_logger.py` 신규)

```python
async def log_event(
    db: AsyncSession,
    event_type: str,
    severity: str,
    message: str,
    server_id: int | None = None,
    server_name: str | None = None,
    metadata: dict | None = None,
) -> None:
    """이벤트를 DB에 저장하고 필요시 Slack 전송"""
    event = EventLog(...)
    db.add(event)
    await db.commit()

    if severity in ("warning", "critical"):
        await _maybe_slack(event)  # 기존 쿨다운 로직 재사용
```

#### 3-3. `server_collector.py` 변경

**프로세스 변화 감지** (GPU users 비교)
```python
# _collect_once() 내부, serverStates 업데이트 전
prev_users = {gpu.index: set(gpu.users) for gpu in self._prev_gpu_data}
curr_users = {gpu.index: set(gpu.users) for gpu in gpu_data.gpus}

for idx in curr_users:
    added   = curr_users[idx] - prev_users.get(idx, set())
    removed = prev_users.get(idx, set()) - curr_users[idx]
    for user in added:
        await log_event(..., event_type="process_start", ...)
    for user in removed:
        await log_event(..., event_type="process_end", ...)

self._prev_gpu_data = gpu_data.gpus
```

**연결 경고 감지** (기존 상태 전환 로직에 추가)
```python
# offline_since 기반으로 warning/alert 분리
elapsed = (now - self._offline_since).total_seconds() / 60
if 0 < elapsed < 3 and not self._warned:
    await log_event(..., event_type="connection_warning", ...)
    self._warned = True
elif elapsed >= 3 and not self._alerted:
    await log_event(..., event_type="connection_alert", ...)
    self._alerted = True
```

**서버 상태 전환** (기존 Slack 알림 위치에 추가)
```python
# 기존: await slack_client.send_alert(...)
# 추가:
await log_event(..., event_type="server_offline" | "server_online" | "server_degraded", ...)
```

#### 3-4. 7일 정리 (`server_collector.py` 또는 `main.py` 스케줄러)

```python
async def cleanup_old_logs(db: AsyncSession):
    cutoff = datetime.utcnow() - timedelta(days=7)
    await db.execute(delete(EventLog).where(EventLog.created_at < cutoff))
    await db.commit()
```

기존 `archive_interval` 루프 (60초마다)에 포함.

#### 3-5. 로그 API (`routers/logs.py` 신규)

```
GET /logs
  Query: server_id?, severity?, limit=50, offset=0
  Response: { items: EventLog[], total: int }

GET /logs/recent
  Query: since=<ISO timestamp>  ← 폴링용
  Response: EventLog[]
```

**응답 스키마**
```python
class EventLogOut(BaseModel):
    id: int
    server_id: int | None
    server_name: str | None
    event_type: str
    severity: str
    message: str
    metadata: dict | None
    created_at: datetime
```

### Frontend 구현

#### 3-6. 타입 추가 (`types.ts`)

```typescript
export type EventSeverity = 'info' | 'warning' | 'critical';
export type EventType =
  | 'server_offline' | 'server_online' | 'server_degraded'
  | 'connection_warning' | 'connection_alert'
  | 'process_start' | 'process_end';

export interface EventLog {
  id: number;
  server_id: number | null;
  server_name: string | null;
  event_type: EventType;
  severity: EventSeverity;
  message: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}
```

#### 3-7. API 함수 추가 (`api.ts`)

```typescript
export async function getLogs(params?: {
  server_id?: number;
  severity?: EventSeverity;
  limit?: number;
  offset?: number;
}): Promise<{ items: EventLog[]; total: number }> { ... }
```

#### 3-8. 새 컴포넌트: `LogPanel.svelte`

```
Props:
  open: boolean (기본 false, 클릭으로 토글)

상태:
  logs: EventLog[]
  loading: boolean
  hasMore: boolean
  filterServer: number | null
  filterSeverity: EventSeverity | null

기능:
  - 패널 열릴 때 getLogs({ limit: 50 }) 호출
  - severity 배지: info=회색, warning=노랑, critical=빨강
  - 서버 이름 필터 드롭다운
  - "더 보기" 버튼 (offset 증가)
  - 자동 갱신 없음 (수동 새로고침 버튼 제공)

렌더링 (row 당):
  [severity badge] [server_name] [message]  [time ago]
```

#### 3-9. `+page.svelte` 통합

- 헤더에 "로그" 버튼 추가 (클릭 시 하단 패널 토글)
- `<LogPanel bind:open={logOpen} />` 대시보드 하단에 배치
- 초기 로드와 완전히 분리 (로그는 별도 요청)

#### 변경 파일
- `backend/models.py` — EventLog 테이블 추가
- `backend/event_logger.py` — **신규**: 이벤트 기록 유틸
- `backend/routers/logs.py` — **신규**: 로그 API
- `backend/main.py` — logs 라우터 등록
- `backend/collectors/server_collector.py` — 이벤트 기록 호출
- `frontend/src/lib/types.ts` — EventLog 타입 추가
- `frontend/src/lib/api.ts` — getLogs 함수 추가
- `frontend/src/lib/components/LogPanel.svelte` — **신규**
- `frontend/src/routes/+page.svelte` — LogPanel 통합

---

## 4. GPU 카드 레이아웃 개선

### 현재 문제
- GpuBar 행1에 온도 + 전력이 있어 가로 공간 많이 차지
- 사용자 행(행2)이 상대적으로 눈에 안 띔
- 온도/전력은 "GPU 퍼포먼스 지표"가 아닌 "하드웨어 상태 지표" → 시스템 섹션이 더 적절

### 개선 후 레이아웃

#### GpuBar (간결하게)
```
행1: [GPU N]  [████░░  67%]  [████████░░  8.2/24 GB]
행2: [indent]  alice, bob
```
- 온도/전력 제거
- 사용률 바, 메모리 바 정보만 (바 폭 약간 넓혀서 가독성 향상)
- 사용자 행은 활성(파랑) / 비활성(회색 "idle") 명확히

#### ServerCard 시스템 섹션 (`시스템` 접기 영역)

```
CPU    [████░░  45%]
RAM    [██████░  12.3 / 24 GB]

── GPU 하드웨어 ──
GPU 0  72°C  180 W
GPU 1  68°C  165 W
GPU 2  55°C   90 W
```
- 기존 CPU/RAM 진행바는 유지
- 구분선 아래 GPU별 온도 + 전력 텍스트 행 추가
- 숫자만 표시 (진행바 없음) → 경량

### 구현 세부사항

#### `GpuBar.svelte` 변경
- `showPower` prop 제거 (더이상 여기서 전력 표시 안 함)
- `temperature` 표시 제거
- 행1: GPU 인덱스, 사용률 바(`w-28`), 메모리 바(`w-36`)로 조정
- 행2: 사용자 텍스트 (현재 유지)

#### `ServerCard.svelte` 변경
- 시스템 섹션(`sysExpanded` 블록)에 GPU 하드웨어 행 추가:
```svelte
{#if server.gpus.length > 0}
  <div class="mt-2 border-t border-white/5 pt-2 space-y-1">
    {#each server.gpus as gpu}
      <div class="flex items-center justify-between text-xs">
        <span class="text-white/40">GPU {gpu.index}</span>
        <div class="flex gap-3 font-mono">
          <span class="{tempClass(gpu.temperature)}">{gpu.temperature}°C</span>
          <span class="text-white/40">{Math.round(gpu.power_draw)} W</span>
        </div>
      </div>
    {/each}
  </div>
{/if}
```

- `GpuBar`에 `showPower` prop 전달 제거

#### 변경 파일
- `frontend/src/lib/components/GpuBar.svelte` — 온도/전력 제거, 바 폭 조정
- `frontend/src/lib/components/ServerCard.svelte` — 시스템 섹션에 GPU 온도/전력 추가

---

## 구현 우선순위

| 순위 | Feature | 이유 |
|---|---|---|
| 1 | GPU 카드 레이아웃 | 이미 동작 중인 화면, 변경 범위 작음, 즉시 체감 |
| 2 | 수동 새로고침 | 버튼 하나, 10분 작업 |
| 3 | 서버 삭제 UI | 백엔드 완성됨, 프론트 컴포넌트만 |
| 4 | 이벤트 로그 | 범위 가장 큼, 백엔드 신규 테이블/라우터 포함 |

---

## 작업 범위 요약

| 파일 | 상태 | 내용 |
|---|---|---|
| `backend/models.py` | 수정 | EventLog 테이블 |
| `backend/event_logger.py` | **신규** | 이벤트 기록 유틸 |
| `backend/routers/logs.py` | **신규** | 로그 API |
| `backend/main.py` | 수정 | logs 라우터 등록 |
| `backend/collectors/server_collector.py` | 수정 | 프로세스/상태 이벤트 기록 |
| `frontend/src/lib/types.ts` | 수정 | EventLog 타입 |
| `frontend/src/lib/api.ts` | 수정 | getLogs, deleteServer는 기존에 있음 |
| `frontend/src/lib/components/GpuBar.svelte` | 수정 | 온도/전력 제거 |
| `frontend/src/lib/components/ServerCard.svelte` | 수정 | 시스템 섹션 GPU 하드웨어 |
| `frontend/src/lib/components/ServerDeleteModal.svelte` | **신규** | 서버 삭제 모달 |
| `frontend/src/lib/components/LogPanel.svelte` | **신규** | 로그 패널 |
| `frontend/src/routes/+page.svelte` | 수정 | 버튼들, 모달 연결 |
