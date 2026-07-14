# Rounded GPU Card Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine the default server card so each GPU is readable at a glance through a coherent GPU map, promoted model/VRAM, primary-contrast GPU users, exactly one thin utilization track on occupied/shared rows, memory as integer text only, free-row tonal treatment, and an unboxed footer, without changing server functionality or user visibility.

**Architecture:** Keep the existing card-based dashboard and masonry layout. Limit source changes to `frontend/src/lib/components/ServerCard.svelte`, `frontend/src/lib/components/GpuBar.svelte`, and `frontend/src/lib/styles/monitor-cards.css`; preserve current data flow, note handling, and server actions, but rebalance hierarchy and surface treatments so model/VRAM and the GPU map lead, GPU usernames regain primary contrast, free rows read as available capacity, and host/timestamps/system/storage/notes remain secondary. The repo has no frontend unit or browser test harness, so use fail-first visual baselines plus `svelte-check` and production build output as the regression gate.

**Tech Stack:** Svelte 5 runes, TypeScript, existing SvelteKit/Vite scripts (`npm run check`, `npm run build`, `npm run dev`, `npm run preview`), CSS variables in `monitor-cards.css`, browser screenshot QA.

---

### Task 1: Promote model/VRAM and replace visible GPU availability counts with a coherent GPU map

**Files:**
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/GpuBar.svelte`

- [ ] **Step 1: Capture the baseline before changing markup**

  Run: `cd frontend && npm run dev -- --host 0.0.0.0 --port 5173`

  Expected: Vite serves the dashboard at `http://127.0.0.1:5173/`.

  Open the page and capture dark/light screenshots at `1440x1000` and `390x844`. Expected baseline failures: the card still uses boxed `G#` styling, the footer still reads as stacked boxes, and the GPU hierarchy still lets telemetry compete with the user row and header.

- [ ] **Step 2: Reorder the header hierarchy in `ServerCard.svelte`**

  Move GPU model and VRAM into the top visible hierarchy near the server name and health. Keep network visibility behavior unchanged, preserve host/IP and refresh metadata as secondary, and make the availability signal a visible GPU map only. Remove any visible strings such as `3 available`, `3 free`, or `free 3/8`; if counts remain for screen readers, keep them in aria text only. Map states are exact: free GPU = filled semantic availability/accent mark; occupied/shared GPU = thin quiet neutral ring. The map must read as one connected set, with no pill/chip/table-cell wrapper.

- [ ] **Step 3: Simplify `GpuBar.svelte` into a true per-GPU row**

  Remove the boxed `G#` badge, keep user names fully visible, wrapping, and primary-contrast, and downgrade host/timestamps/system metadata to secondary. Free rows use accent `사용 가능` plus a borderless subtle gradient/tonal field, not a box. Occupied/shared rows restore exactly one thin utilization track; memory is integer text only. Preserve the existing GPU list loop, accessibility label, and data shape so all current functionality still renders.

- [ ] **Step 4: Run the static frontend gate**

  Run: `cd frontend && npm run check`

  Expected: `svelte-check found 0 errors and 0 warnings`.

### Task 2: Flatten the interior card surface and footer styling

**Files:**
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/GpuBar.svelte`

- [ ] **Step 1: Remove the boxy interior treatments**

  Replace nested borders, stacked glass, divider-heavy sections, and empty low-contrast card bodies with one quiet but legible card interior. Keep the rounded outer shell, but visually collapse the GPU list, system block, storage block, and notes area into a continuous surface. Cards with at least one free GPU get a subtle theme-color upper wash/accent; full cards remain neutral.

- [ ] **Step 2: Rebalance the metric styling**

  Keep numeric labels in integer GB where memory is shown, preserve tabular numerals, and keep semantic colors restrained. Self-review the prior no-equal-dual-bars rule: occupied/shared rows may show exactly one thin utilization track; memory must be integer text only and must not render as a second bar/track.

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

  Expected: each GPU shows exactly one unboxed GPU-map mark; free marks are filled accent and occupied/shared marks are thin quiet rings; the map reads as coherent rather than decorative/disconnected; the visible card no longer shows GPU availability count text; cards with at least one free GPU have a subtle theme-color upper wash/accent while full cards remain neutral; model and VRAM are promoted; GPU usernames use primary contrast and wrap without truncation; free rows show accent `사용 가능` on a borderless subtle gradient/tonal field; occupied/shared rows have exactly one thin utilization track; memory is integer text only; the footer is unboxed; and there is no boxy `G#`, equal-strength dual-bar, or heavy-divider treatment remaining.

- [ ] **Step 3: Run exact acceptance grep checks**

  Run: `git diff -- frontend/src/lib/components/ServerCard.svelte frontend/src/lib/components/GpuBar.svelte frontend/src/lib/styles/monitor-cards.css`

  Expected: no visible availability-count copy is introduced; the GPU map uses filled free marks and ring occupied/shared marks; free-card upper wash/accent is conditional on at least one free GPU; full-card styling remains neutral; occupied/shared rows have one utilization track; memory is text only and not a second bar; free rows use `사용 가능` and a borderless tonal field.

  Run: `grep -RInE "[0-9]+[[:space:]]*(available|free)|available[[:space:]]*[0-9]+|free[[:space:]]*[0-9]+" frontend/src/lib/components frontend/src/lib/styles || true`

  Expected: no visible UI strings or style hooks implement a `3 available`/`3 free`/`free 3/8`-style count. Aria-only labels may summarize counts if visually hidden.

- [ ] **Step 4: Run a full behavior smoke on the existing dashboard**

  Confirm the dashboard still loads server cards, keeps current user-visible functionality intact, and preserves notes, server actions, all-user visibility, dark/light mode, mobile layout, and reduced-motion/accessibility behavior.

  Expected: no server-card functionality regresses while the visual hierarchy changes.

- [ ] **Step 5: Confirm production isolation**

  Run: `pwd && git branch --show-current && git diff --name-only`

  Expected: working path is `~/workspace/monitoring_v2_dev`, branch is `feature/apple-dashboard-refinement`, changed implementation files are limited to the planned frontend files, and no files under the production repository are modified, committed, or deployed.
