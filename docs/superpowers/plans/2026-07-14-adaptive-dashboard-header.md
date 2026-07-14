# Adaptive Dashboard Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a compact unified dashboard header that combines network filters with dashboard controls and minimizes/reveals predictably on scroll.

**Architecture:** Keep all scroll and interaction state in `+page.svelte`; reuse existing stores and modal state as lock inputs. Add narrowly-scoped CSS classes in `app.css` for mode transforms, the reveal strip, responsive layout, and reduced-motion behavior.

**Tech Stack:** Svelte 5 runes/stores, TypeScript, existing CSS/Tailwind utility classes; no dependency.

---

### Task 1: Add header state and scroll behavior

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Test: browser scroll smoke test

- [ ] Add `HeaderMode` and state for mode, previous scroll position, direction accumulator, animation-frame id, pointer/header focus lock, and actions-menu state.
- [ ] Attach a passive `window` scroll listener only in the existing browser runtime path; schedule calculations through `requestAnimationFrame`. On direction change reset the accumulator; while locked, reset it and retain the current non-minimized mode. In cleanup remove the listener and `cancelAnimationFrame` when scheduled.
- [ ] Implement exact thresholds from the design spec and force compact reveal before header focus can be hidden.
- [ ] Use existing `adminOpen`, `deleteOpen`, and view-menu state as lock-open conditions. Header focus/hover/menu locks `compact` except at page top, where it locks `expanded`; modal backdrop keeps header visually compact but non-interactive.
- [ ] Verify at top, intentional down-scroll, up-scroll, focus, hover, and reduced motion.

### Task 2: Merge network filter into the header

**Files:**
- Modify: `frontend/src/routes/+page.svelte`

- [ ] Move the existing `$tabOptions` controls from the standalone `<nav>` into the dashboard header.
- [ ] Preserve filter handler/store behavior and add `aria-pressed` to each filter button.
- [ ] Remove the standalone nav without changing `currentServers` or tab counts.
- [ ] Add an actions menu that keeps delete/debug/view/theme controls reachable on narrow headers.
- [ ] Verify each filter updates cards and active state exactly as before.

### Task 3: Add responsive adaptive-header styling

**Files:**
- Modify: `frontend/src/app.css`

- [ ] Add mode classes for expanded, compact, and minimized header states; use transform/opacity without layout-jump.
- [ ] Add 6px visual grip plus a keyboard-focusable, labelled 24px minimum reveal control. In minimized mode ordinary header controls are inert; activating the reveal control restores compact mode and moves focus to the visible header.
- [ ] At `768px`, switch to compact mobile layout: expanded two rows, compact current-filter menu, actions menu.
- [ ] Add `prefers-reduced-motion` rules that disable transforms/transitions and never enter `minimized`; header is expanded only at the page top and compact otherwise, with all controls reachable.
- [ ] Preserve dark/light/rose theme styling by extending existing dashboard selectors rather than duplicating whole theme blocks.

### Task 4: Validate and commit

**Files:**
- Modify: `frontend/src/routes/+page.svelte`, `frontend/src/app.css`

- [ ] Run `npm run check`.
- [ ] Run `npm run build`.
- [ ] Use the development page through the SSH tunnel at 320px, 768px, and desktop widths; verify header/filter/action access and the exact scroll thresholds. Verify keyboard-only reveal, focus lock while menu/modal is open, `aria-pressed` filter state, inert minimized controls, reduced-motion behavior, and compact mobile filter/actions-menu parity.
- [ ] Verify the development API remains live and the production monitoring stack is still up.
- [ ] Commit the implementation with a focused message.
