# GPU Occupancy Handoff Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Smooth GPU user enter/leave/change presentation without delaying telemetry or changing dashboard geometry.

**Architecture:** Stabilize user ordering at the collector and both frontend presentation boundaries, then use keyed identity layers in `GpuBar` and `CompactServerRow` with Svelte built-in transitions. Extend Full G# and Compact slot state CSS transitions and reduced-motion coverage; do not add timers or dependencies.

**Tech Stack:** Python unittest, Svelte 5, Svelte built-in `fly`, CSS, Node source-contract tests, Playwright CLI.

---

### Task 1: Stabilize GPU user identity ordering

**Files:**
- Create: `backend/tests/test_gpu_users.py`
- Modify: `backend/collectors/gpu.py`

- [ ] Write failing unittest cases proving both gpustat and nvidia-smi parsers emit sorted unique usernames.
- [ ] Run `.venv/bin/python -m unittest backend.tests.test_gpu_users -v`; expect ordering assertions to fail.
- [ ] Replace set-to-list conversions with `sorted(...)` at the collector boundary.
- [ ] Re-run the targeted backend test; expect pass.
- [ ] Run the full backend unittest suite.

### Task 2: Add a height-stable identity handoff

**Files:**
- Modify: `frontend/src/lib/components/GpuBar.contract.test.ts`
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`
- Modify: `frontend/src/lib/components/GpuBar.svelte`
- Modify: `frontend/src/lib/components/CompactServerRow.svelte`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/styles/monitor-compact.css`

- [ ] Add failing contract assertions for sorted Full/Compact display users, stable signatures, keyed identity slot/sets, Svelte `fly` in/out parameters, Full G# and Compact slot color transitions, and reduced-motion handling.
- [ ] Run `node --test --experimental-strip-types src/lib/components/GpuBar.contract.test.ts src/lib/components/compact-dashboard-task4.contract.test.ts src/lib/styles/monitor-cards.contract.test.ts`; expect missing transition-contract failures.
- [ ] Implement sorted display users and keyed identity markup in both views using `fly`, `cubicOut`, and `prefersReducedMotion.current`; use sorted ownership for Compact tooltip/ARIA.
- [ ] Add grid-overlaid identity-slot CSS with existing wrap behavior and 240ms Full G#/Compact slot surface transitions.
- [ ] Disable Full G# and Compact slot transitions in reduced-motion media blocks.
- [ ] Re-run the exact targeted Node command, then `npm run check` and `npm run build`.

### Task 3: Runtime and browser verification

**Files:**
- No additional production files expected.

- [ ] Restart only DEV backend on port 8101 so deterministic ordering is active; do not touch LIVE.
- [ ] Use Playwright against `http://127.0.0.1:5174`; disable WebSocket, intercept DEV `/api/servers/status`, and cycle one GPU through `idle → user`, `user → idle`, and `user A → user B`. Assert telemetry text updates immediately while identity/surface animations are active in Full and Compact.
- [ ] Repeat the intercepted transition under reduced-motion emulation and assert identity motion duration is zero and CSS surface transition is disabled. Verify Full/Compact geometry and 390px overflow.
- [ ] Run `node --test --experimental-strip-types $(find src -name "*.test.ts" | sort)`, `.venv/bin/python -m unittest discover -s backend/tests -p "test_*.py"`, `npm run check`, `npm run build`, and `git diff --check`.
- [ ] Obtain independent code/UX review.
- [ ] Commit on `feature/compact-gpu-dashboard`; verify DEV and LIVE health and LIVE clean HEAD `c50f9d2`.
