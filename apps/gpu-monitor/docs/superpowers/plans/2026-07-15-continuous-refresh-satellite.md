# Continuous Refresh Satellite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple the ten-second refresh visualization from response timing, keep the collapsed indicator inside the viewport, and replace Compact solid occupied fills with restrained tint.

**Architecture:** `RefreshRing.svelte` becomes a stateless health-dot, track, fixed marker, and orbiting satellite. `+page.svelte` owns a fixed request cadence that schedules the next tick before executing the current request, while CSS owns uninterrupted visual timing. Existing shared GPU state values remain unchanged; only Compact presentation changes.

**Tech Stack:** Svelte 5, TypeScript, CSS, Node test runner, Playwright browser QA.

## Global Constraints

- Do not modify or restart the live `monitoring_v2` service on port `5173`.
- Add no dependencies.
- Preserve server order, Full/Compact view selection, Grid/Masonry selection, notes, and holds.
- Visual cadence is exactly `10_000ms`; periodic request lead is exactly `1_000ms`.
- Do not overlap duplicate status requests when one is already in flight.
- Respect `prefers-reduced-motion: reduce`.

---

### Task 1: Stateless satellite indicator and independent polling cadence

**Files:**
- Modify: `frontend/src/lib/components/RefreshRing.svelte`
- Modify: `frontend/src/lib/components/RefreshRing.contract.test.ts`
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify: `frontend/src/header-css-conflict.contract.test.ts`

**Interfaces:**
- Consumes: existing `attention` and `variant` ring properties.
- Produces: a response-independent `10s` satellite animation and `startPollingCadence()` lifecycle.

- [ ] **Step 1: Write failing contracts**

Assert that `RefreshRing` has no `cycleKey`, renders a fixed top marker and satellite, and CSS uses `10s linear infinite`. Assert that polling schedules its next fixed tick before invoking `reloadDashboard()` and never restarts visual state after response completion.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
node --experimental-strip-types --test src/lib/components/RefreshRing.contract.test.ts src/routes/page-view.contract.test.ts src/header-css-conflict.contract.test.ts
```

Expected: existing keyed orbit and response-synchronized scheduler assertions fail.

- [ ] **Step 3: Implement the satellite and cadence**

Remove keyed progress properties, render the SVG marker and satellite, and use `animation: ops-refresh-satellite-orbit 10s linear infinite`. Replace response-driven rescheduling with a lead timeout followed by a fixed ten-second interval. Keep the in-flight guard but never let it alter the next cadence tick.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all focused tests pass.

### Task 2: Restrained Compact occupied styling and viewport containment

**Files:**
- Modify: `frontend/src/lib/styles/monitor-compact.css`
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify: `frontend/src/header-css-conflict.contract.test.ts`

**Interfaces:**
- Consumes: existing `data-state='available' | 'occupied' | 'unknown'` values.
- Produces: occupied tint, dark available slots, neutral unknown slots, and an indicator fully inside all tested viewports.

- [ ] **Step 1: Write failing CSS contracts**

Assert occupied backgrounds use `color-mix()` with a minority accent percentage rather than `background: var(--chart-2)`. Assert tablet and mobile visible ring bounds begin inside the viewport while preserving the content gutter.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
node --experimental-strip-types --test src/lib/components/compact-dashboard-task4.contract.test.ts src/header-css-conflict.contract.test.ts
```

Expected: solid occupied fill and negative visible bounds fail the contracts.

- [ ] **Step 3: Implement restrained surfaces and placement**

Use a low-percentage accent tint for occupied slots, a dark outlined surface for available slots, and neutral muted unknown slots. Resize and align the floating ring so its painted bounds remain inside `0..viewportWidth` and outside card content.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all focused tests pass.

### Task 3: Browser and complete verification

**Files:**
- Modify only files required by failures found during QA.

- [ ] **Step 1: Run browser QA**

At `1440px`, `900px`, and `390px`, verify the center dot breathes, the satellite advances continuously across a delayed response, the indicator remains inside the viewport without card overlap, and Compact occupied slots are tinted rather than solid.

- [ ] **Step 2: Run complete verification**

```bash
find src -name '*.test.ts' -print | sort | xargs node --experimental-strip-types --test
npm run check
npm run build
cd ..
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py' -v
```

Expected: all frontend tests, Svelte diagnostics, build, and backend tests pass.

- [ ] **Step 3: Commit implementation**

```bash
git add frontend/src docs/superpowers/specs/2026-07-15-continuous-refresh-satellite-design.md docs/superpowers/plans/2026-07-15-continuous-refresh-satellite.md
git commit -m "fix: clarify refresh cadence and compact occupancy"
```

