# Dashboard Motion and Refresh Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add coherent layout motion, a response-synchronized refresh ring, a left-side collapsed indicator, and one GPU occupancy color language across Full and Compact views.

**Architecture:** Keep the existing Svelte page scheduling and Masonry action, but give them explicit visual-cycle and FLIP responsibilities. Add a small shared refresh-ring component and a focused layout-motion utility; reuse the existing GPU availability classifier instead of creating another status model.

**Tech Stack:** Svelte 5 runes, TypeScript, CSS custom properties/animations, Web Animations API, Node contract tests, Playwright browser QA.

## Global Constraints

- Do not modify or restart the live `monitoring_v2` service on port 5173.
- Add no dependencies.
- Preserve saved server order and both Full/Compact view preferences.
- Available and occupied use one shared accent in inverse treatments in every view: available is dark with accent text/border; occupied is accent-filled with near-black text; unknown/stale is neutral.
- Respect `prefers-reduced-motion: reduce`.

---

### Task 1: Shared refresh ring and completion-synchronized cycle

**Files:**
- Create: `frontend/src/lib/components/RefreshRing.svelte`
- Create: `frontend/src/lib/components/RefreshRing.contract.test.ts`
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`

**Interfaces:**
- Consumes: `cycleKey: number`, `durationMs: number`, `attention: boolean`, `variant: 'header' | 'floating'`.
- Produces: a decorative center dot plus SVG progress ring whose animation restarts only when `cycleKey` changes.

- [ ] **Step 1: Write failing contract tests**

Assert that `RefreshRing.svelte` uses SVG circles with `pathLength="1"`, the page renders it in header and floating locations, the old `.ops-refresh-cadence` markup is absent, and CSS uses a fixed-length arc with a linear forwards orbit animation.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
node --experimental-strip-types --test src/lib/components/RefreshRing.contract.test.ts src/routes/page-view.contract.test.ts
```

Expected: failures for missing `RefreshRing.svelte` and remaining horizontal cadence markup.

- [ ] **Step 3: Implement the minimal refresh cycle**

Add page state for `refreshCycleKey` and `refreshCycleDurationMs`. `scheduleNextRefresh()` updates these only after a completed refresh or a visibility-driven reschedule. When an auto-refresh trigger finds an active request, retry shortly without resetting the cadence cycle. Render the same ring component in the expanded header and collapsed indicator.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command and expect all focused tests to pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/RefreshRing.svelte frontend/src/lib/components/RefreshRing.contract.test.ts frontend/src/routes/+page.svelte frontend/src/routes/page-view.contract.test.ts frontend/src/lib/styles/monitor-dashboard.css
git commit -m "feat: synchronize refresh ring with polling"
```

### Task 2: Left-side collapsed indicator

**Files:**
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify: `frontend/src/lib/styles/monitor-dashboard.contract.test.ts`
- Modify: `frontend/src/lib/utils/headerVisibility.test.ts`

**Interfaces:**
- Consumes: existing `ops-header-indicator-visible` class and `RefreshRing` output.
- Produces: a fixed left-gutter indicator whose panel opens toward the page interior.

- [ ] **Step 1: Write failing CSS contract tests**

Assert that the anchor remains mounted with opacity/visibility hiding, the indicator uses left alignment instead of `margin-left: auto`, and the panel is anchored with `left: 0; right: auto`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
node --experimental-strip-types --test src/lib/styles/monitor-dashboard.contract.test.ts src/lib/utils/headerVisibility.test.ts
```

Expected: left-alignment and persistent-mount assertions fail.

- [ ] **Step 3: Implement left placement and synchronized hiding**

Use fixed positioning, opacity/visibility transitions, page-gutter alignment on desktop, and a narrow-screen edge fallback. Keep pointer events disabled until visible.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command and expect all focused tests to pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/styles/monitor-dashboard.css frontend/src/lib/styles/monitor-dashboard.contract.test.ts frontend/src/lib/utils/headerVisibility.test.ts
git commit -m "fix: keep collapsed status on the left"
```

### Task 3: Unified GPU state semantics

**Files:**
- Modify: `frontend/src/lib/components/GpuBar.svelte`
- Modify: `frontend/src/lib/components/GpuBar.contract.test.ts`
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/CompactServerRow.svelte`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/styles/monitor-compact.css`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`

**Interfaces:**
- Consumes: `getCompactGpuState(server.status, server.last_seen, gpu)`.
- Produces: `data-state='available' | 'occupied' | 'unknown'` in Full and Compact GPU elements.

- [ ] **Step 1: Write failing semantic tests**

Assert Full receives the shared state from `ServerCard`, Full and Compact both emit the same `data-state`, and CSS maps available and occupied to inverse treatments of one accent while unknown remains neutral.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
node --experimental-strip-types --test src/lib/components/GpuBar.contract.test.ts src/lib/components/compact-dashboard-task4.contract.test.ts src/lib/styles/monitor-cards.contract.test.ts
```

Expected: Full shared-state and inverse-accent assertions fail.

- [ ] **Step 3: Implement the shared state mapping**

Compute the state in `ServerCard`, pass it into `GpuBar`, replace `data-active` styling with `data-state`, and update Full and Compact styling to use the same single-accent inverse cue.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command and expect all focused tests to pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/GpuBar.svelte frontend/src/lib/components/GpuBar.contract.test.ts frontend/src/lib/components/ServerCard.svelte frontend/src/lib/components/CompactServerRow.svelte frontend/src/lib/styles/monitor-cards.css frontend/src/lib/styles/monitor-compact.css frontend/src/lib/styles/monitor-cards.contract.test.ts frontend/src/lib/components/compact-dashboard-task4.contract.test.ts
git commit -m "style: unify gpu availability semantics"
```

### Task 4: FLIP animation for layout changes

**Files:**
- Create: `frontend/src/lib/utils/layoutFlip.ts`
- Create: `frontend/src/lib/utils/layoutFlip.test.ts`
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`

**Interfaces:**
- Produces: `documentRect(element)`, `flipDelta(previous, next)`, and `animateFlip(element, delta, reducedMotion)`.
- Consumes: keyed dashboard card elements inside the existing Masonry action.

- [ ] **Step 1: Write failing utility and integration tests**

Test document-space rectangle normalization, inverse delta calculation, zero-delta bypass, reduced-motion bypass, and the Masonry action’s use of stored rectangles after every placement.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
node --experimental-strip-types --test src/lib/utils/layoutFlip.test.ts src/routes/page-view.contract.test.ts
```

Expected: missing utility and action integration assertions fail.

- [ ] **Step 3: Implement FLIP in the Masonry action**

Store final document-space rectangles after initial layout. On later Grid/Masonry/reflow layouts, capture current visual positions, apply placement, measure final rectangles, cancel only animations for elements that actually move, and animate non-zero inverse offsets for 360ms. Skip all movement for reduced-motion.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command and expect all focused tests to pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/utils/layoutFlip.ts frontend/src/lib/utils/layoutFlip.test.ts frontend/src/routes/+page.svelte frontend/src/routes/page-view.contract.test.ts frontend/src/lib/styles/monitor-dashboard.css
git commit -m "feat: animate dashboard layout reflow"
```

### Task 5: Browser QA and complete verification

**Files:**
- Modify only files required by failures found during QA.

- [ ] **Step 1: Run browser interaction checks**

Verify at 1440px, 900px, and 390px that the left indicator appears after downward scroll, its panel stays on screen, Grid ↔ Gapless visibly moves cards, the refresh arc holds at the top during an artificially delayed response, and Full/Compact colors have identical meanings.

- [ ] **Step 2: Run complete frontend and backend verification**

```bash
find src -name "*.test.ts" -print | sort | xargs node --experimental-strip-types --test
npm run check
npm run build
cd ..
.venv/bin/python -m unittest discover -s backend/tests -p "test_*.py" -v
```

Expected: zero failures, zero Svelte diagnostics, successful build.

- [ ] **Step 3: Verify services without touching live code**

Confirm dev `5174`, dev `/api/health`, live `5173`, and live `/api/health` all return HTTP 200 and that each PID has the expected repository working directory.

- [ ] **Step 4: Commit any QA-only fixes**

```bash
git add <only-files-modified-by-qa>
git commit -m "fix: polish dashboard motion edge cases"
```
