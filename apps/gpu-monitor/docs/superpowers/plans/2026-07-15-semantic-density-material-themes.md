# GPU Monitor Semantic Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Full and Compact immediately legible for GPU selection by removing unexplained UI, adding trustworthy I/O pressure, clarifying memo/hold semantics, and making material/theme motion coherent.

**Architecture:** Extend the existing collector payload with optional Linux PSI fields while preserving legacy parsing. Keep the current Svelte component structure, replace accent-only theme state with style presets, and implement motion through CSS/Svelte state without new dependencies. Tests lock user-visible contracts before each implementation slice.

**Tech Stack:** Python `unittest`, FastAPI payloads, Svelte 5, TypeScript stores, CSS custom properties, Node 24 `node:test` source contracts, Playwright CLI.

---

### Task 1: Verify the documented design contract

**Files:**
- Verify: `docs/superpowers/specs/2026-07-15-semantic-density-material-themes-design.md`
- Verify: `docs/superpowers/plans/2026-07-15-semantic-density-material-themes.md`
- Modify: `DESIGN.md`

- [x] Add the new spec to the active source-of-truth list.
- [x] Supersede collector/theme/motion clauses that conflict with the new direction.
- [x] Commit the documentation before implementation.

### Task 2: Add Linux PSI I/O pressure through TDD

**Files:**
- Create: `backend/tests/test_system_metrics.py`
- Modify: `backend/collectors/system.py`
- Modify: `backend/collectors/server_collector.py`
- Modify: `frontend/src/lib/types.ts`

- [ ] Write failing `unittest` cases for six-field PSI output, malformed/missing PSI values, unsupported kernels, and legacy three-field output.
- [ ] Run `cd /home/ircv/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_system_metrics -v` and verify the expected assertion failure caused by missing PSI behavior.
- [ ] Add optional `io_pressure_some`, `io_pressure_full`, `io_blocked_tasks`, and `io_pressure_supported` fields.
- [ ] Read `/proc/pressure/io` `some/full avg10` plus `/proc/stat` `procs_blocked` in the existing remote command.
- [ ] Keep PSI absence non-degrading and legacy-compatible.
- [ ] Run targeted tests, then `.venv/bin/python -m unittest discover -s backend/tests -v`.
- [ ] Commit.

### Task 3: Rebuild collapsed System, Memo, and Hold hierarchy through TDD

**Files:**
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/GpuBar.svelte`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/components/GpuBar.contract.test.ts`
- Modify: `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`

- [ ] Write failing assertions for no footer dots, one-row System preview, explicit Korean expiry, hold GPU/owner summary, and a Full hold collar.
- [ ] Run `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd /home/ircv/workspace/monitoring_v2_dev/frontend && node --experimental-strip-types --test src/lib/components/GpuBar.contract.test.ts src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts` and verify RED.
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
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`

- [ ] Invert the existing `Full에서 보기` expectation and write failing tests that row activation always opens Full while the hint remains non-interactive.
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
- Modify: `frontend/src/app-css-token.contract.test.ts`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/stores/theme.contract.test.ts` (create only if store behavior cannot be covered in the page contract)

- [ ] Write failing tests for `liquid`, `claude`, `astro`; absence of `blue`, `violet`, `emerald` UI options; and migration of persisted `blue/violet/emerald/rose/pink` values to `liquid`.
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
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/utils/headerVisibility.test.ts`
- Modify: `frontend/src/lib/utils/headerIndicatorLane.test.ts`

- [ ] Write failing tests for a mounted indicator panel without `display: none`, transform/opacity panel transitions, circular-reveal activation lock and cleanup, and reduced-motion behavior.
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

- [ ] Run all frontend contracts exactly: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd /home/ircv/workspace/monitoring_v2_dev/frontend && node --experimental-strip-types --test $(find src -name '*.test.ts' -o -name '*.contract.test.ts' | sort)`.
- [ ] Run `npm run check`.
- [ ] Run `npm run build`.
- [ ] Run full backend tests.
- [ ] Open only the isolated development service with Playwright at `http://127.0.0.1:5174` at desktop and narrow widths; its tmux command must explicitly set `MONITORING_API_TARGET=http://127.0.0.1:8101` and `MONITORING_WS_TARGET=ws://127.0.0.1:8101`.
- [ ] Verify Full/Compact mode switching, exact server order, System/Memo expansion, hold rendering, hover/focus hint, header collapse/reveal, indicator panel, circular mode reveal, and all three style presets.
- [ ] Capture screenshots and inspect console errors.
- [ ] Request designer/code-reviewer verification and fix high-confidence issues.
- [ ] Confirm dev service health. Read only `git -C /home/ircv/workspace/monitoring_v2 status --short` and compare its HEAD/status to the preflight snapshot; do not edit, build, restart, or commit there.
- [ ] Commit the verified result.

## Service-safety guardrails

- Every edit, test, build, and Git command runs from `/home/ircv/workspace/monitoring_v2_dev`.
- Do not modify `/home/ircv/workspace/monitoring_v2`.
- Do not execute or edit `run_monitoring.sh`, deployment scripts, production tmux sessions, production ports, or the Slack bridge.
- The active development tmux endpoint is port `5174` and the development backend is `8101`. If the frontend is restarted, recreate only `monitoring_v2_dev_frontend` with explicit `MONITORING_API_TARGET` and `MONITORING_WS_TARGET` pointing to `8101`; do not fall back to Vite's live `8001` default.
- Before implementation, record the live repo HEAD/status read-only. At completion, compare them and require no change.
