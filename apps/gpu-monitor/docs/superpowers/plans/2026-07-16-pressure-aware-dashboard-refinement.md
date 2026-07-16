# Pressure-Aware Dashboard Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace throughput-based ambiguity with CPU/I/O pressure semantics, keep Masonry columns stable during card height changes, correct failure reveal behavior, shorten expiry copy, and reduce avoidable client work.

**Architecture:** Extend the existing single remote system command with optional CPU PSI/runnable-task plus load-average telemetry. Keep the compact summary centered on raw 1-minute load versus logical CPU count, use normalized load ratio for visual/state logic, retain backend disk-rate fields for API compatibility only, and preserve backward compatibility. Replace stateless Masonry reassignment with identity-based sticky columns plus one-row left bias, then share the existing page clock across Full cards.

**Tech Stack:** Python 3 dataclasses/unittest, Svelte 5 runes, TypeScript utilities, CSS Grid/ResizeObserver/Web Animations, Node test runner, Playwright.

## Global Constraints

- Work only in `/home/ircv/workspace/monitoring_v2_dev` on `feature/compact-gpu-dashboard`.
- Never edit or restart `/home/ircv/workspace/monitoring_v2`; live must remain `c50f9d2`, clean and healthy.
- Keep one system SSH command per server collection cycle and `collect_interval = 10`.
- Keep the frontend fixed 10-second HTTP refresh and visual cadence even when WebSocket is connected.
- CPU pressure uses `/proc/pressure/cpu some avg10`; never use system-wide CPU `full`.
- The frontend removes MB/s from the UI entirely; backend disk-rate fields remain for API compatibility only.
- Thresholds are exact: `<5 = 여유`, `>=5 and <20 = 압박`, `>=20 = 병목`.
- Similar-height left bias is exactly one masonry row.
- Height-only card changes preserve the assigned column; structural/order/column-count changes may recompute.
- Relative active expiry copy omits `남음`; expired copy remains `만료됨`.
- Failure veil is visible over a lightly blurred body by default and completely hidden on hover/focus-within.
- No new dependency.
- Reduced-motion users retain immediate, functional state changes.

---

### Task 1: Add optional CPU pressure telemetry to the existing collector command

**Files:**
- Modify: `backend/collectors/system.py`
- Modify: `backend/collectors/server_collector.py`
- Modify: `backend/collectors/manager.py`
- Modify: `backend/tests/test_system_metrics.py`
- Modify: `backend/tests/test_gpu_health.py`
- Modify if fallback payload requires it: `backend/routers/metrics.py`

**Interfaces:**
- Produces `SystemInfo.cpu_pressure_some: float | None`.
- Produces `SystemInfo.cpu_running_tasks: int | None`.
- Produces `SystemInfo.load_avg_1: float | None`, `load_avg_5: float | None`, `load_avg_15: float | None`.
- Produces `SystemInfo.cpu_count: int | None`.
- Appends CPU pressure/runnable-task as CSV fields 10/11 and load averages/CPU count as fields 12-15, preserving 3/6/10/12/16-field parsing.
- API `system` payload exposes `cpu_pressure_some`, `cpu_running_tasks`, `load_avg_1`, `load_avg_5`, `load_avg_15`, and `cpu_count`.

- [ ] **Step 1: Write failing parser and command tests**

Add tests equivalent to:

```python
def test_parse_system_reads_cpu_pressure_and_running_tasks(self):
    info = parse_system("12.0,1048576,2097152,1.0,0.2,2,100,200,3.0,4,7.5,6")
    self.assertEqual(info.cpu_pressure_some, 7.5)
    self.assertEqual(info.cpu_running_tasks, 6)

def test_parse_system_reads_load_average_and_cpu_count(self):
    info = parse_system("12.0,1048576,2097152,1.0,0.2,2,100,200,3.0,4,7.5,6,0.8,1.2,1.5,64")
    self.assertEqual(info.load_avg_1, 0.8)
    self.assertEqual(info.load_avg_5, 1.2)
    self.assertEqual(info.load_avg_15, 1.5)
    self.assertEqual(info.cpu_count, 64)

def test_system_command_reads_cpu_some_without_cpu_full(self):
    self.assertIn("/proc/pressure/cpu", SYSTEM_CMD_PROC)
    self.assertIn("procs_running", SYSTEM_CMD_PROC)
    self.assertIn("os.getloadavg", SYSTEM_CMD_PROC)
    self.assertIn("os.cpu_count", SYSTEM_CMD_PROC)
    self.assertNotIn("cpu_pressure_full", SYSTEM_CMD_PROC)
```

Retain explicit legacy parser assertions for 3, 6, 10, and 12 fields.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=. .venv/bin/python -m unittest backend.tests.test_system_metrics backend.tests.test_gpu_health -v
```

Expected: new field/command assertions fail because CPU/load telemetry fields are absent.

- [ ] **Step 3: Implement the minimal collector extension**

Append optional dataclass fields and parse indices 10/11 when `len(parts) >= 12`, then parse load averages/CPU count at indices 12-15 when `len(parts) >= 16`.

Generalize the embedded proc reader so it reads:

```python
cpu_some = read_psi_some_avg10('/proc/pressure/cpu')
io_some, io_full = read_io_psi_avg10()
load1, load5, load15 = os.getloadavg()
cpu_count = os.cpu_count()
```

Return `procs_running` from the existing `/proc/stat` read. Do not invoke another command or sleep.

Expose the optional CPU/load fields in the collector state while keeping existing backend disk-rate fields/API compatibility unchanged.

- [ ] **Step 4: Run GREEN and backend suite**

```bash
PYTHONPATH=. .venv/bin/python -m unittest backend.tests.test_system_metrics backend.tests.test_gpu_health -v
PYTHONPATH=. .venv/bin/python -m unittest discover -s backend/tests -v
```

Expected: all backend tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend
git commit -m "feat: collect cpu pressure telemetry"
```

---

### Task 2: Classify CPU/I/O pressure and update the dense System UI

**Files:**
- Create: `frontend/src/lib/utils/resourcePressure.ts`
- Create: `frontend/src/lib/utils/resourcePressure.test.ts`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/stores/servers.ts`
- Modify: `frontend/src/lib/utils/serverStateMerge.test.ts`
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.css`

**Interfaces:**
- `type PressureLevel = 'unknown' | 'idle' | 'pressure' | 'bottleneck'`
- `classifyPressure(avg10: number | null | undefined): PressureLevel`
- `pressureLabel(level): '–' | '여유' | '압박' | '병목'`
- Frontend `SystemInfo` accepts optional CPU pressure/running fields plus load averages and CPU count.

- [ ] **Step 1: Write pressure boundary tests**

```ts
assert.equal(classifyPressure(null), 'unknown');
assert.equal(classifyPressure(4.9), 'idle');
assert.equal(classifyPressure(5), 'pressure');
assert.equal(classifyPressure(19.9), 'pressure');
assert.equal(classifyPressure(20), 'bottleneck');
```

Add normalization/equality tests proving both new fields survive and trigger meaningful state updates.

Add ServerCard contract assertions:

- collapsed I/O uses `pressureLabel`, not throughput;
- CPU shows utilization plus `압박/병목` only when non-idle;
- expanded detail includes CPU stall, runnable tasks, I/O stall/full/blocked, and R/W MB/s;
- historical/offline placeholders remain;
- `gpu_device_missing` with current freshness retains current pressure.

- [ ] **Step 2: Run RED**

```bash
cd frontend
node --test src/lib/utils/resourcePressure.test.ts src/lib/utils/serverStateMerge.test.ts src/lib/components/ServerCard.note-contract.test.ts
```

Expected: missing module/types/copy assertions fail.

- [ ] **Step 3: Implement the pure classifier and data pipeline**

Use exact thresholds:

```ts
export function classifyPressure(avg10) {
  if (typeof avg10 !== 'number' || !Number.isFinite(avg10)) return 'unknown';
  if (avg10 >= 20) return 'bottleneck';
  if (avg10 >= 5) return 'pressure';
  return 'idle';
}
```

Normalize the optional CPU fields without inventing zero values.

- [ ] **Step 4: Implement the UI hierarchy**

Collapsed compact summary:

```text
CPU 28% · RAM 42% · Storage 63% · 부하 3.2 / 32
```

Keep CPU/RAM/Storage first and percentage-only so they remain readable without increasing card height. Load is trailing diagnostic context, with normalized load ratio (`load_avg_1 / cpu_count`) used for visual/state logic rather than replacing the displayed numerator/denominator. Remove the normal-state load gauge; pressure/bottleneck text can still receive semantic emphasis. CPU PSI and I/O PSI explain the cause in expanded detail. Do not show MB/s anywhere in the UI.

Expanded details show 1/5/15 load plus CPU stall/runnable facts and I/O pressure facts. Keep the layout dense and tabular.

- [ ] **Step 5: Run GREEN/check/build**

```bash
node --test src/lib/utils/resourcePressure.test.ts src/lib/utils/serverStateMerge.test.ts src/lib/components/ServerCard.note-contract.test.ts
npm run check
npm run build
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib
git commit -m "feat: show cpu and io pressure states"
```

---

### Task 3: Add one-row left bias and sticky Masonry columns

**Files:**
- Modify: `frontend/src/lib/utils/orderedMasonry.ts`
- Modify: `frontend/src/lib/utils/orderedMasonry.test.ts`
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/utils/layoutFlip.ts`
- Modify: `frontend/src/lib/utils/layoutFlip.test.ts` if motion contract changes

**Interfaces:**
- Extend `OrderedMasonryInput` with `preferredColumns?: readonly (number | null)[]` and `leftBiasRows?: number`.
- Default `leftBiasRows = 1`.
- Existing output shape remains unchanged.

- [ ] **Step 1: Write failing allocator tests**

Cover:

```ts
// Heights within one row choose earlier/left column.
placeOrderedMasonryItems({ columnCount: 3, spans: [4, 5, 5, 1], leftBiasRows: 1 })

// Preferred columns survive height changes.
placeOrderedMasonryItems({
  columnCount: 3,
  spans: changedSpans,
  preferredColumns: previous.map((item) => item.gridColumnStart)
})
```

Assert DOM order, non-decreasing `gridRowStart`, left bias, and identical columns during height-only reflow.

- [ ] **Step 2: Run RED**

```bash
cd frontend
node --test src/lib/utils/orderedMasonry.test.ts src/routes/page-view.contract.test.ts
```

- [ ] **Step 3: Implement left-biased placement**

For unassigned items:

```ts
const minimum = Math.min(...nextRows);
const eligible = nextRows
  .map((row, index) => ({ row, index }))
  .filter(({ row }) => row <= minimum + leftBiasRows);
const columnIndex = eligible[0].index;
```

For valid preferred columns, use that column. Preserve the monotonic start-row floor.

- [ ] **Step 4: Implement action-level sticky assignment and height cache**

Maintain:

```ts
let assignedColumns = new Map<HTMLElement, number>();
let measuredHeights = new Map<HTMLElement, number>();
let assignedItems: HTMLElement[] = [];
let assignedColumnCount = 0;
```

- Resize entries update only cached heights and schedule one RAF.
- Height-only layout passes `preferredColumns`.
- Child identity/order or column-count changes clear assignments.
- Disabling Masonry clears inline placement and caches.
- Do not clear placement styles before every height measurement.
- FLIP uses the current visual rect if an animation is active.
- With sticky assignments, expand/collapse FLIP deltas must have `x = 0`.

- [ ] **Step 5: Tune layout motion**

Use approximately 400ms and `cubic-bezier(0.22, 1, 0.36, 1)`. Preserve reduced-motion no-op behavior.

- [ ] **Step 6: Run GREEN/check**

```bash
node --test src/lib/utils/orderedMasonry.test.ts src/lib/utils/layoutFlip.test.ts src/routes/page-view.contract.test.ts
npm run check
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/utils frontend/src/routes
git commit -m "fix: stabilize masonry column reflow"
```

---

### Task 4: Correct failure reveal, expiry copy, motion, and timer fan-out

**Files:**
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- Modify: `frontend/src/lib/components/NoteForm.svelte`
- Modify: `frontend/src/lib/components/NoteForm.contract.test.ts`
- Modify: `frontend/src/lib/components/CompactDashboard.svelte`
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/page-view.contract.test.ts`

**Interfaces:**
- Add optional/required `nowMs` prop to `ServerCard`, supplied by the page's existing visibility-aware clock.
- Remove `ServerCard`'s internal interval.
- Keep NoteForm's interval gated by `active`.

- [ ] **Step 1: Write failing copy/veil/timer tests**

Require all active expiry helpers to return `42초`, `12분`, `7시간`, `3일`, never `남음`.

Require CSS hover/focus state:

```css
.monitor-card[data-operational-state='impaired']:is(:hover, :focus-within)
.monitor-card__state-veil {
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
}
```

Require the default body blur to remain light and the default veil to contain label/secondary text.

Require `ServerCard` to consume `nowMs` and contain no `setInterval`.

- [ ] **Step 2: Run RED**

```bash
cd frontend
node --test src/lib/components/ServerCard.note-contract.test.ts src/lib/components/NoteForm.contract.test.ts src/lib/components/compact-dashboard-task4.contract.test.ts src/routes/page-view.contract.test.ts
```

- [ ] **Step 3: Implement copy and shared clock**

Remove only the word `남음`. Keep `만료됨`.

Pass `nowMs` from `+page.svelte`:

```svelte
<ServerCard {server} {nowMs} ... />
```

Use it for freshness and note expiry calculations; remove the per-card effect interval.

- [ ] **Step 4: Implement veil interaction**

Default:

- body blur around 1-1.5px;
- readable opacity around 0.7;
- translucent veil with problem text.

Hover/focus:

- body `filter: none; opacity: 1`;
- veil `opacity: 0; visibility: hidden; backdrop-filter: none`;
- no veil text remains over the content.

Use 240-280ms settling motion and preserve reduced motion.

- [ ] **Step 5: Smooth common component motion**

Apply the same settling curve to disclosure, GPU meter width, card hover, and compact indicator open/close. Keep changes restrained and do not animate top/height directly.

- [ ] **Step 6: Run GREEN/full frontend/check/build**

```bash
node --test $(find src -type f -name "*.test.ts" | sort)
npm run check
npm run build
```

- [ ] **Step 7: Commit**

```bash
git add frontend
git commit -m "refactor: smooth dashboard state transitions"
```

---

### Task 5: Runtime activation, Playwright QA, performance evidence, and reviews

**Files:**
- No committed production file required unless QA finds a regression.
- Temporary Playwright artifacts remain under `/tmp`, not the repository.

- [ ] **Step 1: Run complete verification**

```bash
cd /home/ircv/workspace/monitoring_v2_dev
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm use --silent 24
cd frontend
node --test $(find src -type f -name "*.test.ts" | sort)
npm run check
npm run build
cd ..
PYTHONPATH=. .venv/bin/python -m unittest discover -s backend/tests -v
git diff --check
```

- [ ] **Step 2: Restart development backend only**

Restart only `monitoring_v2_dev_backend` on port 8101 using the existing command. Never restart live.

Wait for at least two 10-second samples and verify every reachable server payload includes optional CPU pressure/running fields when kernel support exists.

- [ ] **Step 3: Playwright interaction and geometry QA**

Verify:

- Full Grid and Masonry at 1440x1000;
- card DOM order matches API/user order;
- one-row-left-bias fixture;
- System expand/collapse preserves every card's `x` coordinate and only changes `y`;
- no jump at disclosure transition end;
- collapsed CPU/I/O pressure copy and expanded R/W context;
- failure veil default screenshot;
- hover/focus screenshot has hidden veil and sharp body;
- expiry copy has no `남음`;
- Compact and Full at 390x900 have no horizontal overflow;
- reduced-motion smoke path.

- [ ] **Step 4: Measure bounded optimization**

Collect:

- active `ServerCard` interval count from source/runtime contract: zero;
- one page freshness ticker;
- Masonry layout remains RAF-coalesced;
- no new SSH command and collector interval remains 10;
- browser page errors: zero.

- [ ] **Step 5: Independent task and final review**

Require:

- backend telemetry spec PASS;
- Masonry behavior spec PASS;
- UI/UX screenshot review APPROVED;
- final code review APPROVED;
- independent verifier VERIFIED.

- [ ] **Step 6: Verify service and repository isolation**

```bash
# DEV
git -C ~/workspace/monitoring_v2_dev status --short
curl -fsS http://127.0.0.1:8101/health
curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:5174

# LIVE
test "$(git -C ~/workspace/monitoring_v2 rev-parse --short HEAD)" = "c50f9d2"
test -z "$(git -C ~/workspace/monitoring_v2 status --short)"
curl -fsS http://127.0.0.1:8001/health
curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:5173
```

- [ ] **Step 7: Final integration commit if QA required fixes**

Use one focused commit only when QA changes production files. Otherwise leave the reviewed task commits as final history.
