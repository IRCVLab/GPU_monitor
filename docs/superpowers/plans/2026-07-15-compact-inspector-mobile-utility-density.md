# Compact Inspector and Mobile Utility Density Implementation Plan

> **For agentic workers:** Follow red-green-refactor. Do not edit or restart the live repository/service.

**Goal:** Recover vertical density in Full mobile cards and prevent Compact desktop details from obscuring the availability overview.

**Architecture:** Keep the current Svelte data flow, ordered server store, Compact row component, and mobile sheet. Change only presentation contracts: Full collapsed footer flex geometry and Compact desktop detail placement/mode.

**Tech Stack:** Svelte 5, TypeScript, CSS, Node test runner, SvelteKit/Vite, Playwright CLI.

## Global constraints

- Development repository only.
- No new dependencies.
- No server-order, telemetry, note, hold, or refresh-cadence behavior changes.
- Preserve Compact availability color semantics and one server per row.
- Commit documentation before implementation.

---

### Task 1: Lock Full mobile utility density

**Files:**
- Modify: `frontend/src/lib/styles/monitor-cards.contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.css`

- [ ] Add a failing contract that the `max-width: 640px` footer toggle remains a row, stays centered, and keeps preview truncation/right alignment.
- [ ] Run the targeted contract and confirm RED because the current rule forces `column` and `flex-start`.
- [ ] Remove the column layout and make the label/side shrink behavior explicit.
- [ ] Run the targeted contract and confirm GREEN.

### Task 2: Lock non-obscuring Compact desktop detail

**Files:**
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`
- Modify: `frontend/src/lib/components/CompactDashboard.svelte`
- Modify: `frontend/src/lib/components/CompactServerDetail.svelte`
- Modify: `frontend/src/lib/styles/monitor-compact.css`

- [ ] Replace the old overlay contract with failing assertions for an in-flow inspector, inspector mode, and desktop two-column selected state.
- [ ] Keep assertions for the mobile modal sheet and one-row server list.
- [ ] Run the targeted contract and confirm RED against `.compact-detail-overlay` / `mode="overlay"`.
- [ ] Add a selected-state class to the Compact dashboard, render a labelled inspector region in flow, and rename the desktop detail mode.
- [ ] Add desktop-only grid columns and restrained inspector entrance motion; remove fixed overlay positioning.
- [ ] Run the targeted contract and confirm GREEN.

### Task 3: Browser verification and correction loop

- [ ] At `390x740` and `360x740`, measure footer control direction/height, truncation, and horizontal overflow.
- [ ] At `1440x900` and `1200x800`, open Compact detail and measure list/inspector overlap (`0` required), header overlap, and body overflow.
- [ ] At `1024x768`, verify the bottom sheet remains modal and usable.
- [ ] Verify the visible server order is unchanged across Full and Compact.
- [ ] Verify scroll-down header collapse still leaves the left status indicator inside the viewport.
- [ ] Check browser console errors/warnings and iterate only on evidenced regressions.

### Task 4: Full verification and delivery

- [ ] Run all frontend Node tests.
- [ ] Run `npm run check`.
- [ ] Run `npm run build`.
- [ ] Run backend regression tests.
- [ ] Request independent code/UX review and address material findings.
- [ ] Commit implementation on `feature/compact-gpu-dashboard`.
- [ ] Confirm dev `5174`, local tunnels, and live `5173` all return HTTP 200; do not restart live.
- [ ] Leave the development repository clean.
