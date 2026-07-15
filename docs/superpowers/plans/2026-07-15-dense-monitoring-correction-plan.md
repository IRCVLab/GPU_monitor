# Dense Monitoring Dashboard Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the visible server sequence, restore the indicator-only Full header state, and increase Full/Compact information density while unifying memo and advisory GPU hold interaction.

**Architecture:** Keep the existing Svelte components and backend note schema. Add one pure ordered-masonry placement helper, keep header state in the existing visibility utility, derive active GPU holds inside `ServerCard`, and implement the remaining changes as narrow component/CSS contract updates.

**Tech Stack:** Svelte 5, TypeScript, CSS, Node test runner, SvelteKit/Vite.

**Design source:** `docs/superpowers/specs/2026-07-15-dense-monitoring-correction-design.md`

**Command working directories:** Run every frontend `node`/`npm` command from `~/workspace/monitoring_v2_dev/frontend`. Run backend Python commands from `~/workspace/monitoring_v2_dev`.

---

### Task 1: Stable server placement

**Files:**
- Create: `frontend/src/lib/utils/orderedMasonry.ts`
- Create: `frontend/src/lib/utils/orderedMasonry.test.ts`
- Modify: `frontend/src/routes/+page.svelte:44-108`
- Modify: `frontend/src/routes/page-view.contract.test.ts:84-93`

- [ ] **Step 1: Write failing tests**
  - Assert three columns map items `0..5` to columns `1,2,3,1,2,3`.
  - Assert row starts advance independently per column.
  - Keep the existing `$currentServers` / `serverOrder` assertion unchanged.
  - Add a separate assertion that the page action writes and cleans `gridColumnStart`, `gridRowStart`, and `gridRowEnd`.
- [ ] **Step 2: Run targeted tests and confirm RED**

```bash
node --experimental-strip-types --test src/lib/utils/orderedMasonry.test.ts src/routes/page-view.contract.test.ts
```

- [ ] **Step 3: Implement the pure placement helper and wire it into the existing action**
  - Resolve column count from computed grid columns.
  - Compute spans from measured item heights.
  - Apply deterministic modulo-column placement in DOM order.
  - Clean all three placement properties on destroy.
- [ ] **Step 4: Run targeted tests and confirm GREEN**
- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/utils/orderedMasonry.ts frontend/src/lib/utils/orderedMasonry.test.ts frontend/src/routes/+page.svelte frontend/src/routes/page-view.contract.test.ts
git commit -m "fix: preserve dashboard server placement"
```

### Task 2: Indicator-only Full header

**Files:**
- Modify: `frontend/src/lib/utils/headerVisibility.ts:1-74`
- Modify: `frontend/src/lib/utils/headerVisibility.test.ts:43-263`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css:5-49,218-263,444-478`
- Modify: `frontend/src/routes/+page.svelte:403-460,711-731`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/header-css-conflict.contract.test.ts`

- [ ] **Step 1: Write failing tests**
  - `updateHeaderVisibility()` shows the indicator at exactly 921px when compact.
  - `updateHeaderVisibility()` hides it at exactly 920px.
  - Replace the old page-view/CSS 1199px cutoff contract with the new 920px cutoff and 921–1199px edge-lane contract.
  - Compact CSS does not reopen the complete header on hover/focus.
  - Header surface binds `inert` and `aria-hidden` to compact state.
  - Indicator trigger has no circular container background/border.
- [ ] **Step 2: Run targeted tests and confirm RED**
- [ ] **Step 3: Implement the minimal state/CSS changes**
  - Rename the visibility input to an indicator-lane predicate if needed.
  - Remove compact-shell hover/focus expansion rules.
  - Add inert/aria-hidden to the hidden header only.
  - Keep indicator independently focusable.
  - Use a dot-sized trigger and safe page-edge positioning for 921–1199px.
- [ ] **Step 4: Run targeted tests and confirm GREEN**
- [ ] **Step 5: Commit**

```bash
git commit -am "fix: keep full header collapsed to status dot"
```

### Task 3: One-line Compact rows

**Files:**
- Modify: `frontend/src/lib/styles/monitor-compact.css:29-200,485-527`
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts:55-75`

- [ ] **Step 1: Replace the old one-column contract with failing responsive contracts**
  - Base/tablet row: `minmax(7rem, 8.5rem) minmax(0, 1fr)`.
  - Row min-height at most `2.7rem`.
  - Slot height at most `1.8rem`.
  - Only `max-width:767px` may switch to one column.
- [ ] **Step 2: Run test and confirm RED**
- [ ] **Step 3: Implement CSS-only layout change**
- [ ] **Step 4: Run test and confirm GREEN**
- [ ] **Step 5: Commit**

```bash
git commit -am "fix: keep compact servers on one row"
```

### Task 4: Dense Full footer and System panel

**Files:**
- Modify: `frontend/src/lib/styles/monitor-cards.css:348-629`
- Modify: `frontend/src/routes/page-view.contract.test.ts:61-69`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`

- [ ] **Step 1: Write failing numeric density contracts**
  - Footer gap `0.28rem`; padding `0.5rem 0.75rem 0.55rem`.
  - Second section top padding no more than `0.3rem`.
  - Expanded section top padding `0.45rem`, metric stack gap `0.32rem`.
  - Hardware/mount vertical padding no more than `0.3rem`; radius no more than `0.5rem`.
  - Note item padding/radius aligns with the same density scale.
- [ ] **Step 2: Run tests and confirm RED**
- [ ] **Step 3: Tighten CSS without deleting any System data**
- [ ] **Step 4: Run tests and confirm GREEN**
- [ ] **Step 5: Commit**

```bash
git commit -am "style: densify server card utilities"
```

### Task 5: Unified memo and optional hold composer

**Files:**
- Modify: `frontend/src/lib/components/NoteForm.svelte:17-151,162-210`
- Modify: `frontend/src/lib/components/NoteForm.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.css:905-967`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`

- [ ] **Step 1: Write failing contracts**
  - No memo/hold kind toggle exists.
  - GPU chips are always available as optional attachments.
  - Submit derives `kind` from `selectedGpuIndices.length`.
  - Empty selection sends memo with no GPUs; non-empty selection sends hold.
  - Concise Korean advisory copy appears only when GPUs are selected.
- [ ] **Step 2: Run tests and confirm RED**
- [ ] **Step 3: Remove mode state and implement derived payload behavior**
- [ ] **Step 4: Run tests and confirm GREEN**
- [ ] **Step 5: Commit**

```bash
git commit -am "feat: unify memo and gpu hold composer"
```

### Task 6: Show hold on the affected GPU row

**Files:**
- Modify: `frontend/src/lib/components/ServerCard.svelte:74-199,324-329,441-504`
- Modify: `frontend/src/lib/components/GpuBar.svelte:1-54`
- Create: `frontend/src/lib/components/GpuBar.contract.test.ts`
- Modify: `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.css:210-348,968-1007`

- [ ] **Step 1: Write failing contracts**
  - `ServerCard` derives unexpired hold notes by GPU index and passes them to `GpuBar`.
  - `GpuBar` accepts hold metadata and renders a textual `HOLD` cue beside users/idle.
  - Dedicated `GpuBar.contract.test.ts` asserts hold metadata does not participate in `isActive`, utilization, memory, or `data-active`.
  - Preview/history uses concise hold terminology while retaining GPU indices in details.
- [ ] **Step 2: Run tests and confirm RED**
- [ ] **Step 3: Implement derived mapping and compact GPU-row cue**
- [ ] **Step 4: Run tests and confirm GREEN**
- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/GpuBar.contract.test.ts frontend/src/lib/components/GpuBar.svelte frontend/src/lib/components/ServerCard.svelte frontend/src/lib/components/ServerCard.note-contract.test.ts frontend/src/lib/styles/monitor-cards.css
git commit -m "feat: surface soft holds on gpu rows"
```

### Task 7: Full verification and browser QA

**Files:**
- Update: `docs/superpowers/plans/2026-07-15-dense-monitoring-correction-plan.md` with evidence notes only if needed.

- [ ] **Step 1: Run all frontend Node tests**

```bash
find src -name '*.test.ts' -print | sort | xargs node --experimental-strip-types --test
```

- [ ] **Step 2: Run static checks and build**

```bash
npm run check
npm run build
```

- [ ] **Step 3: Run backend regression tests**

```bash
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py' -v
```

- [ ] **Step 4: Browser QA at 1440×900 and 1024×768**
  - Full order owns stable conceptual columns.
  - Scroll down leaves only the dot indicator.
  - Header controls are absent from Tab order while hidden.
  - System expansion is visibly denser without lost data.
  - Optional GPU selection creates one unified hold path.
  - Hold cue appears on affected GPU rows.
  - Compact is one server row at both widths and has no horizontal scroll.
- [ ] **Step 5: Independent code/design review**
- [ ] **Step 6: Confirm dev repo clean, tmux services healthy, local tunnel 200, and live repo unchanged**
