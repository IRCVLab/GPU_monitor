# Quiet Rack Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Compact as an aligned availability rack and refine Full/header/system/memo hierarchy so researchers can find a trustworthy free GPU at a glance without losing dense detail.

**Architecture:** Keep the existing SvelteKit route, stores, API, server order, and theme tokens. Add a small pure GPU-bank helper, simplify Compact to one shared matrix plus a bounded micro-popover, and refine existing Full components/CSS rather than creating another design-system layer. Every behavior change starts with a failing Node contract/unit test and ends with Playwright evidence.

**Tech Stack:** SvelteKit 5, Svelte 5 runes, TypeScript, existing Tailwind/CSS layers, Node 24 built-in `node:test`, Svelte check, Vite build, Python unittest, and Playwright CLI.

**Execution Environment:** The canonical Git checkout and complete frontend/backend tree are on `ircv@166.104.167.11:~/workspace/monitoring_v2_dev`. The local path `/Users/shchoi/workspace/.tmp-dev-correction/work` is an edit/Playwright mirror without `.git` or backend files. Sync changed files to the canonical remote before every test/commit command; every bare `git`, `node`, `npm`, `.venv`, or backend command below runs from the canonical remote checkout unless explicitly labeled local Playwright.

## Global Constraints

- Work only in `~/workspace/monitoring_v2_dev` on `feature/compact-gpu-dashboard`.
- Run Git, frontend tests, backend tests, and commits in the canonical remote checkout; never run Git or backend commands in the local mirror.
- Do not edit, rebuild, restart, or deploy `~/workspace/monitoring_v2`.
- Preserve manual server order in Full and Compact; never auto-sort.
- Compact shows availability only and uses no prominent textual free count.
- At `390x844`, Compact keeps one server per row, eight aligned GPU columns, and no horizontal page/row scroll.
- Available is dark/outlined; occupied is filled with the selected color-theme accent; unknown is muted; absent is a quiet placeholder.
- Normal server state is a small green dot; health and GPU state remain separate.
- No Compact inspector, reserved detail column, or large bottom sheet.
- Full density stays fixed at the prior small density; Grid/Masonry remains a Full-only user choice.
- Use one selected-theme hue for Full Util and Mem, differentiated by geometry/opacity rather than a second hue.
- Add no dependency and change no collector or WebSocket payload contract.
- Use `DESIGN.md` and `docs/superpowers/specs/2026-07-15-quiet-rack-gpu-monitor-design.md` as the active design contract.

---

## Task 1: Lock the Compact Matrix Data Contract

**Files:**
- Create: `frontend/src/lib/utils/compactGpuMatrix.ts`
- Create: `frontend/src/lib/utils/compactGpuMatrix.test.ts`

**Interfaces:**
- Produces: `COMPACT_GPU_BANK_SIZE`, `compactGpuBankCount(servers)`, `compactGpuBankSlots(gpus, bankIndex)`.
- Consumes: existing `GpuInfo` and `ServerState` types.

- [ ] **Step 1: Write failing helper tests**

```ts
test('uses one eight-slot bank for G0-G7', () => {
  assert.equal(compactGpuBankCount([{ gpus: [{ index: 7 }] }]), 1);
});

test('adds a second bank when G8 exists', () => {
  assert.equal(compactGpuBankCount([{ gpus: [{ index: 8 }] }]), 2);
});

test('returns exact slots and null absent placeholders', () => {
  const slots = compactGpuBankSlots([{ index: 0 }, { index: 3 }], 0);
  assert.deepEqual(slots.map((gpu) => gpu?.index ?? null), [0, null, null, 3, null, null, null, null]);
});
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd frontend
node --experimental-strip-types --test src/lib/utils/compactGpuMatrix.test.ts
```

Expected: failure because `compactGpuMatrix.ts` and its exports do not exist.

- [ ] **Step 3: Implement the pure helper**

```ts
export const COMPACT_GPU_BANK_SIZE = 8;

export function compactGpuBankCount(servers: Pick<ServerState, 'gpus'>[]): number {
  const maxIndex = Math.max(-1, ...servers.flatMap((server) => server.gpus.map((gpu) => gpu.index)));
  return Math.max(1, Math.floor(maxIndex / COMPACT_GPU_BANK_SIZE) + 1);
}

export function compactGpuBankSlots(gpus: GpuInfo[], bankIndex: number): Array<GpuInfo | null> {
  const start = bankIndex * COMPACT_GPU_BANK_SIZE;
  const byIndex = new Map(gpus.map((gpu) => [gpu.index, gpu]));
  return Array.from({ length: COMPACT_GPU_BANK_SIZE }, (_, offset) => byIndex.get(start + offset) ?? null);
}
```

- [ ] **Step 4: Run helper tests and all utility tests**

Expected: helper tests pass and existing availability/order tests remain green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/utils/compactGpuMatrix.ts frontend/src/lib/utils/compactGpuMatrix.test.ts
git commit -m "test: define compact gpu matrix contract"
```

## Task 2: Replace Compact Rows with a Fixed Availability Rack

**Files:**
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`
- Modify: `frontend/src/lib/components/CompactDashboard.svelte`
- Modify: `frontend/src/lib/components/CompactServerRow.svelte`
- Delete: `frontend/src/lib/components/CompactServerDetail.svelte`
- Replace: `frontend/src/lib/styles/monitor-compact.css`

**Interfaces:**
- Consumes: Task 1 helpers and `getCompactGpuState`.
- Consumes when available: optional `heldGpuIndicesByServer: ReadonlyMap<number, ReadonlySet<number>>`; absence means no Compact hold overlay and never changes telemetry state.
- Produces: shared `G#` header, fixed eight-slot rows, absent placeholders, bank selector, optional hold notches, micro-popover, and row-level mobile activation.

- [ ] **Step 1: Replace the old inspector/mobile-wrap assertions with failing matrix assertions**

The contract test must assert:

```ts
assert.doesNotMatch(dashboardSource, /CompactServerDetail|compact-dashboard__inspector|compact-sheet/);
assert.match(dashboardSource, /compactGpuBankCount/);
assert.match(dashboardSource, /compactGpuBankSlots/);
assert.match(dashboardSource, /compact-dashboard__column-header/);
assert.match(rowSource, /data-state="absent"|data-state=\{'absent'\}/);
assert.match(rowSource, /data-held/);
assert.doesNotMatch(rowSource, />\{statusConfig\[server\.status\].*label/);
assert.doesNotMatch(cssSource, /repeat\(auto-fit/);
assert.match(cssSource, /repeat\(8,\s*minmax\(22px,\s*1fr\)\)/);
assert.doesNotMatch(cssSource, /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
```

Also assert available uses `var(--ops-primary)` outline, occupied uses selected-theme fill, unknown has no primary/green token, and absent is noninteractive/transparent.

- [ ] **Step 2: Run the contract test and verify RED**

Expected: failures reference old inspector, `auto-fit`, mobile stacking, and absent placeholders.

- [ ] **Step 3: Simplify `CompactDashboard.svelte`**

- Remove selected server, desktop media query, row reference map, inspector, and bottom sheet state.
- Add derived bank count and active bank.
- Accept optional held-GPU sets without fetching notes globally or changing availability truth.
- Render one shared column header using `bankStart + offset`.
- Render a bank selector only when `bankCount > 1`.
- Keep a single bounded tooltip/popover state for occupied usernames and optional Full transition.
- Keep server iteration exactly `{#each servers as server (server.server_id)}`.

- [ ] **Step 4: Rebuild `CompactServerRow.svelte`**

- Accept `bankIndex`, render eight values from `compactGpuBankSlots`.
- Accept the server’s optional held-index set and emit `data-held="true"` only for matching real GPU cells.
- Render absent placeholders with `aria-hidden="true"`.
- Remove visible normal status text; retain the status dot and accessible row label.
- Add `has-available` only when a fresh available GPU exists in the visible bank.
- Use deterministic initials only for occupied slots.
- At mobile widths, disable per-cell pointer behavior and use the row button as the touch target.

- [ ] **Step 5: Replace Compact CSS with the rack contract**

- Board max width `60rem`, centered.
- Shared grid template with `clamp(4.5rem, 18vw, 8.25rem) repeat(8, minmax(22px, 1fr))`.
- Row min-height `2.35rem` desktop and `2.5rem` mobile.
- No mobile wrapping rule.
- Available uses strong primary outline and aperture.
- Occupied uses restrained primary fill with dark initials.
- `has-available::before` renders a 2px left rail.
- Unknown uses muted texture/mark; absent preserves geometry without visual weight.
- `[data-held='true']::after` renders a noninteractive `2px` amber corner notch without overriding the base state.
- Popover remains `180-220px`, max `120px`, viewport clamped.

- [ ] **Step 6: Run targeted tests and Svelte check**

Expected: Compact contracts pass, Svelte check reports zero errors/warnings.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/components frontend/src/lib/styles/monitor-compact.css
git commit -m "feat: rebuild compact as availability rack"
```

## Task 3: Make View Controls Contextual and Keep Order Stable

**Files:**
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/lib/components/CompactDashboard.svelte`
- Modify: `frontend/src/lib/components/CompactServerRow.svelte`

**Interfaces:**
- Consumes: existing `dashboardView`, `dashboardLayout`, and ordered `currentServers`.
- Produces: Full-only Grid/Masonry controls and optional Compact-to-Full focus target.

- [ ] **Step 1: Add failing route contract assertions**

```ts
assert.match(pageSource, /\{#if \$dashboardView === 'default'\}[\s\S]*카드 배치/);
assert.match(pageSource, /<CompactDashboard[\s\S]*onOpenFull=/);
assert.match(pageSource, /focusedServerId/);
assert.match(pageSource, /\{#each \$currentServers as server \(server\.server_id\)\}/);
```

- [ ] **Step 2: Verify RED**

Expected: layout controls are currently unconditional and Compact has no Full handoff callback.

- [ ] **Step 3: Implement minimal route changes**

- Wrap Grid/Masonry menu group and its divider in `if dashboardView === 'default'`.
- Thread an `onOpenFull(serverId)` callback through `CompactDashboard.svelte` and `CompactServerRow.svelte`; the route callback switches to Full, stores the server id, waits for render, and focuses/scrolls that card without reordering.
- Add a temporary focus class that fades after continuity is established.

- [ ] **Step 4: Run route tests and check**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/+page.svelte frontend/src/routes/page-view.contract.test.ts
git commit -m "feat: align dashboard view controls"
```

## Task 4: Unify Full GPU Hierarchy and Metric Color

**Files:**
- Modify: `frontend/src/lib/components/GpuBar.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/components/GpuBar.svelte`
- Modify: `frontend/src/lib/styles/monitor-cards.css`

**Interfaces:**
- Produces: one theme-accent state language shared with Compact and a wider Mem track.

- [ ] **Step 1: Change tests first**

Assertions must require:

```ts
assertDeclaration(availableIndexRule, 'color', 'var(--ops-primary)');
assertDeclaration(occupiedIndexRule, 'background', 'var(--ops-primary)');
assertDeclaration(utilRule, 'background', 'var(--ops-primary)');
assert.match(memoryRule, /var\(--ops-primary\)/);
assert.doesNotMatch(memoryRule, /var\(--chart-1\)|var\(--chart-2\)/);
assertDeclaration(metricsRule, 'grid-template-columns', 'minmax(0, 0.72fr) minmax(0, 1.28fr)');
```

- [ ] **Step 2: Verify RED**

Expected: current Util uses `chart-2`, Mem uses `chart-1`, and state badges do not follow selected color theme.

- [ ] **Step 3: Implement minimal semantic CSS**

- Use `--ops-primary` for available outline and occupied fill.
- Use `--ops-primary-foreground`/dark token for occupied index text.
- Use primary at full strength for Util and a lower opacity/color-mix for Mem.
- Keep Mem’s numeric column at `8ch` and increase its flexible track share to `1.28fr`.
- Keep idle zero tracks visible but quiet.

- [ ] **Step 4: Run card/route tests and check**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/GpuBar* frontend/src/lib/styles/monitor-cards* frontend/src/routes/page-view.contract.test.ts
git commit -m "style: unify full gpu state hierarchy"
```

## Task 5: Refine Card Header and System Density

**Files:**
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`

**Interfaces:**
- Produces: exception-only status text, fixed collapsed system micro-columns, and denser expanded telemetry rows.

- [ ] **Step 1: Add failing component/CSS contracts**

- Online status text must be visually hidden while degraded/offline/unknown remain visible.
- Collapsed system preview must render four named items instead of one punctuation-delimited string.
- Footer toggle min-height must be at most `1.875rem`.
- Hardware and mount rows must remain `1.5-1.625rem` high on mobile.
- Expanded summary must use a compact grid and no nested card background.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement card header and system markup**

Render the collapsed system preview as:

```svelte
<span class="monitor-card__system-preview-item"><small>CPU</small><strong>{cpuPct.toFixed(0)}%</strong></span>
<span class="monitor-card__system-preview-item"><small>RAM</small><strong>{ramPct.toFixed(0)}%</strong></span>
<span class="monitor-card__system-preview-item"><small>GPU</small><strong>{totalGpuPowerText}</strong></span>
<span class="monitor-card__system-preview-item"><small>Disk</small><strong>{storagePct.toFixed(0)}%</strong></span>
```

Use data-level threshold attributes for warning color and keep normal values muted.

- [ ] **Step 4: Tighten expanded CSS**

- Use a compact summary grid.
- Remove redundant subsection boxes.
- Keep GPU hardware and mount rows at `24-26px`.
- Keep tabular alignment and existing storage warning thresholds.

- [ ] **Step 5: Run card tests, check, and build**

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/components/ServerCard.svelte frontend/src/lib/styles/monitor-cards.css frontend/src/lib/styles/monitor-cards.contract.test.ts
git commit -m "style: refine server card hierarchy"
```

## Task 6: Compress Memo and Hold Interaction

**Files:**
- Modify: `frontend/src/lib/components/NoteForm.contract.test.ts`
- Modify: `frontend/src/lib/components/NoteForm.svelte`
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`

**Interfaces:**
- Preserves existing Note API and advisory hold payload.
- Produces a maximum three-row composer and one-line collapsed preview.

- [ ] **Step 1: Add failing memo density contracts**

- General hold explanation copy is absent when telemetry is fresh and server online.
- Warning copy remains conditional for stale/abnormal telemetry.
- Composer uses compact GPU scope, identity/content, and expiry/submit rows.
- Collapsed hold preview places `G#` scope before owner/content and keeps expiry fixed right.
- Existing `buildNotePayload` memo/hold behavior remains unchanged.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Simplify `NoteForm.svelte` and card preview**

- Remove the always-visible advisory explanation.
- Keep only conditional stale/status warning.
- Preserve GPU chips and automatic memo/hold kind.
- Reorder collapsed hold scope before user/content.
- Keep active hold cue attached directly to the corresponding Full GPU row.

- [ ] **Step 4: Tighten form CSS and run tests**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/NoteForm* frontend/src/lib/components/ServerCard.svelte frontend/src/lib/styles/monitor-cards*
git commit -m "style: compress memo and hold workflow"
```

## Task 7: Make Header Indicator Viewport-Safe

**Files:**
- Modify: `frontend/src/header-css-conflict.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify only if required: `frontend/src/routes/+page.svelte`

**Interfaces:**
- Preserves existing continuous ten-second orbit and six-second breath.
- Produces a safe collapsed indicator at desktop and mobile widths.

- [ ] **Step 1: Add failing safe-inset assertions**

- Indicator anchor exists at narrow widths.
- Left and top use clamped safe insets, never negative transforms.
- Trigger and panel fit inside `100vw`.
- Header shell collapses its reserved height when compact.
- No visible in-flight text is introduced.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement safe geometry**

- Remove rules that hide or push the indicator outside narrow viewports.
- Anchor to the page/content gutter with `left: clamp(0.75rem, 2vw, 1rem)` and safe top variables.
- Clamp the panel to `calc(100vw - 1.5rem)` and align left on mobile.
- Keep orbit/breath animations independent of request response.

- [ ] **Step 4: Run header tests, check, and build**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/header-css-conflict.contract.test.ts frontend/src/lib/styles/monitor-dashboard.css frontend/src/routes/+page.svelte
git commit -m "fix: keep collapsed status indicator in viewport"
```

## Task 8: Playwright Visual Refinement Loop

**Files:**
- Create/update evidence only under: `output/playwright/quiet-rack/`
- Modify earlier frontend files only when a reproduced visual defect has a failing regression test.

- [ ] **Step 1: Run the full automated baseline**

```bash
ssh -p 2200 -o BatchMode=yes ircv@166.104.167.11 '
  set -e
  cd ~/workspace/monitoring_v2_dev
  export NVM_DIR="$HOME/.nvm"
  . "$NVM_DIR/nvm.sh"
  nvm use --silent 24
  cd frontend
  node --experimental-strip-types --test $(find src -name "*.test.ts" -print | sort)
  npm run check
  npm run build
  cd ..
  .venv/bin/python -m unittest discover -s backend/tests -p "test_*.py" -v
  git diff --check
'
```

- [ ] **Step 2: Capture dark screenshots**

- `1440x900`: Full Grid, Full Masonry, Compact.
- `1024x768`: Compact.
- `390x844`: Full and Compact.
- Capture scrolled header/indicator state and expanded System/Memo state.

- [ ] **Step 3: Capture light screenshots**

Repeat Compact and representative Full at `1440x900` and `390x844`.

- [ ] **Step 4: Measure acceptance invariants with Playwright**

- `document.documentElement.scrollWidth === window.innerWidth` at all target widths.
- Every Compact row has one bounding rectangle line and no wrapped slot group.
- G0-G7 cell x-coordinates match across 2/4/8-GPU servers.
- Current 11-server Compact fleet fits inside `390x844` without ordinary page scrolling.
- Collapsed indicator bounds stay within viewport and do not overlap first content row.
- No console errors/warnings caused by the redesign.

- [ ] **Step 5: Test exceptional states**

- Use route interception or local fixtures for unknown/offline, duplicate initials, and synthetic `G8`.
- Verify mandatory bank selector, preserved order, and no silent GPU omission.
- Verify available/occupied/unknown/absent remain distinguishable in dark/light and without relying on hue alone.

- [ ] **Step 6: Iterate only from reproduced evidence**

For each defect: write a failing contract/regression test, verify RED, patch minimally, verify GREEN, and recapture the affected viewport.

## Task 9: Final Review and Commit

**Files:**
- All changed frontend files and active docs.

- [ ] **Step 1: Run final verification from a clean shell**

Repeat the complete remote Task 8 automated baseline and inspect full output.

- [ ] **Step 2: Run independent code/design review**

Review for server-order regressions, stale-to-unknown safety, mobile touch semantics, hidden `G8+`, accidental live-repo changes, and unnecessary abstraction.

- [ ] **Step 3: Verify service boundaries**

- Development frontend/backend respond on `5174`/`8001` through existing tunnels.
- Live frontend/backend still respond on `5173`/`8000` and their repo/process state was not modified.

- [ ] **Step 4: Commit final polish if the working tree contains reviewed fixes**

```bash
ssh -p 2200 -o BatchMode=yes ircv@166.104.167.11 '
  cd ~/workspace/monitoring_v2_dev
  git add DESIGN.md docs/superpowers frontend
  git commit -m "feat: refine gpu availability experience"
'
```

- [ ] **Step 5: Report evidence**

Report changed files, design simplifications, test counts, Playwright viewport results, commit hashes, and any remaining risk without claiming unsupported visual perfection.
