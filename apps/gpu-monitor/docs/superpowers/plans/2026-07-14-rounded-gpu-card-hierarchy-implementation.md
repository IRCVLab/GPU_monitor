# Rounded GPU Card Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the default server card so each GPU is readable at a glance through unboxed availability dots, promoted model/VRAM, quieter telemetry, and an unboxed footer, without changing server functionality or user visibility.

**Architecture:** Keep the existing card-based dashboard and masonry layout. Limit source changes to `frontend/src/lib/components/ServerCard.svelte`, `frontend/src/lib/components/GpuBar.svelte`, and `frontend/src/lib/styles/monitor-cards.css`; preserve current data flow, note handling, and server actions, but rebalance hierarchy and surface treatments so model/VRAM and per-GPU dots lead while utilization, memory, system, storage, and notes become secondary. The repo has no frontend unit or browser test harness, so use fail-first visual baselines plus `svelte-check` and production build output as the regression gate.

**Tech Stack:** Svelte 5 runes, TypeScript, existing SvelteKit/Vite scripts (`npm run check`, `npm run build`, `npm run dev`, `npm run preview`), CSS variables in `monitor-cards.css`, browser screenshot QA.

---

### Task 1: Promote model/VRAM and replace visible GPU availability counts

**Files:**
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/GpuBar.svelte`

- [ ] **Step 1: Capture the baseline before changing markup**

  Run: `cd frontend && npm run dev -- --host 0.0.0.0 --port 5173`

  Expected: Vite serves the dashboard at `http://127.0.0.1:5173/`.

  Open the page and capture dark/light screenshots at `1440x1000` and `390x844`. Expected baseline failures: the card still uses boxed `G#` styling, the footer still reads as stacked boxes, and the GPU hierarchy still lets telemetry compete with the user row and header.

- [ ] **Step 2: Reorder the header hierarchy in `ServerCard.svelte`**

  Move GPU model and VRAM into the top visible hierarchy near the server name and health. Keep network visibility behavior unchanged, preserve host/IP and refresh metadata, and make the availability signal a visible dot strip only. Remove any visible strings such as `3 available`, `3 free`, or `free 3/8`; if counts remain for screen readers, keep them in aria text only.

- [ ] **Step 3: Simplify `GpuBar.svelte` into a true per-GPU row**

  Remove the boxed `G#` badge, keep user names fully visible and wrapping, and downgrade utilization/memory to quieter secondary text instead of equal-strength paired bars. Preserve the existing GPU list loop, accessibility label, and data shape so all current functionality still renders.

- [ ] **Step 4: Run the static frontend gate**

  Run: `cd frontend && npm run check`

  Expected: `svelte-check found 0 errors and 0 warnings`.

### Task 2: Flatten the interior card surface and footer styling

**Files:**
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/GpuBar.svelte`

- [ ] **Step 1: Remove the boxy interior treatments**

  Replace nested borders, stacked glass, and divider-heavy sections with one quiet card interior. Keep the rounded outer shell, but visually collapse the GPU list, system block, storage block, and notes area into a continuous surface.

- [ ] **Step 2: Rebalance the metric styling**

  Reduce the prominence of utilization and memory tracks so they no longer read as twin primary bars. Keep numeric labels in integer GB where memory is shown, preserve tabular numerals, and keep the semantic colors restrained.

- [ ] **Step 3: Quiet the footer while preserving behavior**

  Restyle the system toggle, notes toggle, note preview, note list, note form, and delete affordances so they read as a calm disclosure area instead of separate boxed widgets. Preserve hover/focus access, note loading/error/retry behavior, and existing delete/password interactions.

- [ ] **Step 4: Run the production build**

  Run: `cd frontend && npm run build`

  Expected: `vite v6.0.7 building for production...` followed by a successful bundle and exit code 0.

### Task 3: Visual QA, regression sweep, and isolation check

**Files:**
- Test: `frontend/src/lib/components/ServerCard.svelte`
- Test: `frontend/src/lib/components/GpuBar.svelte`
- Test: `frontend/src/lib/styles/monitor-cards.css`

- [ ] **Step 1: Serve the built app for final visual QA**

  Run: `cd frontend && npm run preview -- --host 0.0.0.0 --port 4173`

  Expected: Vite serves the production build at `http://127.0.0.1:4173/`.

- [ ] **Step 2: Capture the final screenshot set**

  Open `http://127.0.0.1:4173/` and inspect dark/light themes at `1440x1000` and `390x844`.

  Expected: each GPU shows exactly one unboxed dot; the visible card no longer shows GPU availability count text; model and VRAM are promoted; usernames wrap without truncation; utilization and memory are secondary; the footer is unboxed; and there is no boxy `G#`, equal-strength dual-bar, or heavy-divider treatment remaining.

- [ ] **Step 3: Run a full behavior smoke on the existing dashboard**

  Confirm the dashboard still loads server cards, keeps current user-visible functionality intact, and preserves notes, server actions, and all-user visibility.

  Expected: no server-card functionality regresses while the visual hierarchy changes.

- [ ] **Step 4: Confirm production isolation**

  Verify that only `~/workspace/monitoring_v2_dev` is used for this pass and that `~/workspace/monitoring_v2` is untouched.

  Expected: no files under the production repository are modified, committed, or deployed.
