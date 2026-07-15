# GPU Monitor Semantic Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Full and Compact immediately legible for GPU selection by removing unexplained UI, adding trustworthy I/O pressure, clarifying memo/hold semantics, and making material/theme motion coherent.

**Architecture:** Extend the existing collector payload with optional Linux PSI fields while preserving legacy parsing. Keep the current Svelte component structure, replace accent-only theme state with style presets, and implement motion through CSS/Svelte state without new dependencies. Tests lock user-visible contracts before each implementation slice.

**Tech Stack:** Python collector/tests, FastAPI payloads, Svelte 5, TypeScript stores, CSS custom properties, Vitest/Node contract tests, Playwright CLI.

---

### Task 1: Document and lock the design contract

**Files:**
- Create: `docs/superpowers/specs/2026-07-15-semantic-density-material-themes-design.md`
- Create: `docs/superpowers/plans/2026-07-15-semantic-density-material-themes.md`
- Modify: `DESIGN.md`

- [ ] Add the new spec to the active source-of-truth list.
- [ ] Supersede collector/theme/motion clauses that conflict with the new direction.
- [ ] Commit the documentation before implementation.

### Task 2: Add Linux PSI I/O pressure through TDD

**Files:**
- Create: `backend/tests/test_system_metrics.py`
- Modify: `backend/collectors/system.py`
- Modify: `backend/collectors/server_collector.py`
- Modify: `frontend/src/lib/types.ts`

- [ ] Write failing tests for PSI parsing, unsupported kernels, and legacy three-field output.
- [ ] Run `cd backend && pytest -q tests/test_system_metrics.py` and verify the expected failure.
- [ ] Add optional `io_pressure_some`, `io_pressure_full`, `io_blocked_tasks`, and `io_pressure_supported` fields.
- [ ] Read `/proc/pressure/io` `some/full avg10` plus `/proc/stat` `procs_blocked` in the existing remote command.
- [ ] Keep PSI absence non-degrading and legacy-compatible.
- [ ] Run targeted and full backend tests.
- [ ] Commit.

### Task 3: Rebuild collapsed System, Memo, and Hold hierarchy through TDD

**Files:**
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/GpuBar.svelte`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: existing card/component contract tests under `frontend/tests/`

- [ ] Write failing assertions for no footer dots, one-row System preview, explicit Korean expiry, hold GPU/owner summary, and a Full hold collar.
- [ ] Run targeted frontend tests and verify RED.
- [ ] Compact healthy server metadata to one line.
- [ ] Render CPU/RAM/I/O/Disk on one System baseline and detailed PSI in expanded content.
- [ ] Normalize memo/hold content order and explicit expiry copy.
- [ ] Add a visible, non-state-replacing hold collar/notch.
- [ ] Run targeted tests and commit.

### Task 4: Make Compact interaction purposeful through TDD

**Files:**
- Modify: `frontend/src/lib/components/CompactServerRow.svelte`
- Modify: `frontend/src/lib/components/CompactDashboard.svelte`
- Modify: `frontend/src/lib/styles/monitor-compact.css`
- Modify: existing Compact contract tests under `frontend/tests/`

- [ ] Write failing tests that row activation always opens Full and the hint contains no `Full에서 보기` action.
- [ ] Verify RED.
- [ ] Make the hint non-interactive and pointer-transparent.
- [ ] Keep exact GPU, owner, state, and hold context only.
- [ ] Strengthen held-cell collar using the same shape language as Full.
- [ ] Verify server order is unchanged, run tests, and commit.

### Task 5: Replace color themes with material presets through TDD

**Files:**
- Modify: `frontend/src/lib/stores/theme.ts`
- Modify: `frontend/src/app.html`
- Modify: `frontend/src/app.css`
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify: theme/token tests under `frontend/tests/`

- [ ] Write failing tests for `liquid`, `claude`, `astro` and absence of `blue`, `violet`, `emerald` UI options.
- [ ] Write failing token assertions for exact Claude+ and AstroVista light/dark values.
- [ ] Verify RED.
- [ ] Migrate persisted old accent values to `liquid`.
- [ ] Implement preset-level radius, surface, shadow, and glass variables.
- [ ] Apply glass only to functional layers and keep content cards opaque.
- [ ] Update the View menu labels and previews.
- [ ] Run targeted tests and commit.

### Task 6: Add coherent header/indicator and mode motion through TDD

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify: motion/header tests under `frontend/tests/`

- [ ] Write failing tests for a mounted indicator panel, transform/opacity panel transitions, theme reveal state, and reduced-motion behavior.
- [ ] Verify RED.
- [ ] Measure header orb and fixed indicator endpoints and animate a FLIP handoff during header collapse/reveal.
- [ ] Replace indicator panel `display` toggling with opacity/transform/visibility/pointer-event transitions.
- [ ] Add button-centered circular mode reveal with activation lock and cleanup.
- [ ] Keep cadence orbit independent from network completion.
- [ ] Run targeted tests and commit.

### Task 7: Verify in a real browser and refine

**Files:**
- Modify only files implicated by reproducible defects.
- Add regression tests before each browser-discovered fix.

- [ ] Run frontend unit/contract tests.
- [ ] Run `npm run check`.
- [ ] Run `npm run build`.
- [ ] Run full backend tests.
- [ ] Open the dev service with Playwright at desktop and narrow widths.
- [ ] Verify Full/Compact mode switching, exact server order, System/Memo expansion, hold rendering, hover/focus hint, header collapse/reveal, indicator panel, circular mode reveal, and all three style presets.
- [ ] Capture screenshots and inspect console errors.
- [ ] Request designer/code-reviewer verification and fix high-confidence issues.
- [ ] Confirm dev/live service health and that the live repository is untouched.
- [ ] Commit the verified result.

