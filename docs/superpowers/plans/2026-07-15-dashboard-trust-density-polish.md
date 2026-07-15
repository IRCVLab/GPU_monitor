# Dashboard Trust and Density Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore backend-owned server order and correct the header, GPU metric, and secondary-control density defects in the development dashboard.

**Architecture:** Keep the existing Svelte components and telemetry data flow. Remove only the page-local ordering override, make header cadence a derived visual value, and implement layout corrections through the existing semantic component classes and contract tests.

**Tech Stack:** Svelte 5, TypeScript, CSS, Node test runner, SvelteKit/Vite.

## Global Constraints

- Development repository only; do not edit or restart the live repository/service.
- No new dependencies.
- Preserve Full/Compact data, note deletion, advisory hold semantics, and accessibility.
- Follow red-green-refactor for every behavior change.

---

### Task 1: Backend-authoritative order and all-width indicator

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify: `frontend/src/lib/utils/headerVisibility.test.ts`

- [ ] Write failing contracts that reject client order/drag code and reject any responsive rule that hides `.ops-indicator-anchor`.
- [ ] Run the targeted tests and confirm they fail for those exact reasons.
- [ ] Derive `currentServers` from the selected backend-sorted store only; remove drag handlers/attributes; keep ordered masonry placement.
- [ ] Remove the 920px indicator suppression and add narrow-width safe positioning.
- [ ] Run the targeted tests and commit.

### Task 2: Header cadence and GPU metric width

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/components/GpuBar.svelte`
- Modify: `frontend/src/lib/components/GpuBar.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-dashboard.css`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`

- [ ] Write failing contracts for a visible semantic health label plus cadence track, hidden exact timing text, unequal metric allocation, retained `GB`, and no `10ch` memory reserve.
- [ ] Run the targeted tests and confirm RED.
- [ ] Add a clamped derived refresh progress value and render the cadence track.
- [ ] Allocate the metric row so Mem receives the larger flexible track and realistic value width.
- [ ] Run the targeted tests and commit.

### Task 3: Refined collapsed utilities and grouped Memo panel

**Files:**
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`

- [ ] Write failing contracts for CSS disclosure angles, section markers, and distinct Memo history/composer groups.
- [ ] Run the targeted tests and confirm RED.
- [ ] Replace glyph chevrons with semantic spans, add quiet markers, and label Memo groups without adding card-like clutter.
- [ ] Preserve deletion, note creation, GPU selection, expiry controls, and hold cues.
- [ ] Run the targeted tests and commit.

### Task 4: Verification

- [ ] Run all frontend Node tests.
- [ ] Run `npm run check` and `npm run build`.
- [ ] Run backend regression tests.
- [ ] Browser-check 390x844, 900x800, 1024x768, and 1440x900 for order, indicator, header cadence, Mem track width, Memo grouping, and horizontal overflow.
- [ ] Confirm the dev tmux services respond and the live public service still returns HTTP 200.
- [ ] Commit any verification-only documentation and leave the dev repository clean.
