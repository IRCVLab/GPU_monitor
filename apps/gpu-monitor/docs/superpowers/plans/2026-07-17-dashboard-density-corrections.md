# Dashboard Density Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove repeated hold-disclaimer noise, show interpreted collapsed load state, keep masonry expansion local to its assigned column, and remove visible V-shortcut clutter while preserving keyboard behavior.

**Architecture:** Keep the existing Svelte component boundaries, server ordering, cookie preferences, and keyboard resolver. Change only presentation derivations and the ordered masonry placement invariant. Masonry columns stay sticky after assignment; height changes recompute row starts independently per column and continue using existing FLIP motion.

**Tech Stack:** Svelte 5, TypeScript, Node test runner contract/unit tests, CSS Grid, ResizeObserver, Web Animations FLIP.

---

### Task 1: Lock copy and collapsed-load behavior

**Files:**
- Modify: `frontend/src/lib/components/NoteForm.contract.test.ts`
- Modify: `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- Modify: `frontend/src/routes/page-view.contract.test.ts`

- [ ] Assert the reservation disclaimer is not repeated in priority descriptions or composer headings and appears at most once conditionally after GPU selection.
- [ ] Assert collapsed System displays a qualitative load label derived from normalized load level, without raw load average or CPU-count text.
- [ ] Assert the Full/Compact trigger retains `aria-keyshortcuts="V"` but has no visible V tooltip or legend.
- [ ] Run focused tests and confirm RED for the intended missing behavior.

### Task 2: Make masonry height changes column-local

**Files:**
- Modify: `frontend/src/lib/utils/orderedMasonry.test.ts`
- Modify: `frontend/src/lib/utils/orderedMasonry.ts`
- Modify: `frontend/src/routes/page-view.contract.test.ts`

- [ ] Add a unit test proving height growth moves only later items assigned to the same column.
- [ ] Remove global `previousGridRowStart` coupling while preserving sticky preferred columns and deterministic initial assignment.
- [ ] Keep DOM/server order unchanged and retain existing FLIP animation.
- [ ] Run focused placement and route contract tests.

### Task 3: Implement compact copy and view-control cleanup

**Files:**
- Modify: `frontend/src/lib/components/NoteForm.svelte`
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Modify: `frontend/src/routes/+page.svelte`

- [ ] Keep one contextual hold disclaimer only after a GPU is selected.
- [ ] Map collapsed load level to `여유 / 보통 / 높음 / –`; keep raw 1/5/15m and PSI values in expanded System.
- [ ] Remove visible `V 보기` tooltip and shortcut legend; preserve keyboard V handling and accessibility metadata.
- [ ] Run all focused tests and confirm GREEN.

### Task 4: Verify and commit

**Files:**
- Verify only.

- [ ] Run all frontend Node tests.
- [ ] Run `npm run check`.
- [ ] Run `npm run build`.
- [ ] Verify DEV UI with Playwright in Full/Masonry mode: copy, load text, menu, and same-column expansion movement.
- [ ] Request code review, address valid findings, confirm clean worktree, and commit to `feature/compact-gpu-dashboard`.
