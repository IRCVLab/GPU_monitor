# Compact GPU Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a frontend-only Compact dashboard view that preserves the existing Default dashboard, manual server order, and live telemetry while adding deterministic Linux-username initials, selected-row details, and accessible mobile/desktop presentation.

**Architecture:** Keep the current Default card dashboard behavior intact and introduce a separate Compact render path behind a cookie-backed `dashboardView` preference. Compact rows reuse the existing server order and telemetry stores, but move the interaction model to a dense server list plus a focused detail panel on desktop and a bottom sheet on mobile. Identity rendering stays isolated in a small TypeScript utility so the row UI can stay declarative.

**Tech Stack:** SvelteKit 5, Svelte 5, TypeScript, Node 24 built-in `node:test`, existing app CSS, no new dependencies.

---

### Global Constraints
- Frontend only.
- Slack integration is fully deferred.
- Default dashboard behavior must remain unchanged.
- Manual server order remains authoritative.
- The view menu must offer only Default / Compact.
- Persist the selected dashboard view in a cookie-backed store named `dashboardView`.
- Keep all memory and utilization values integer-based in Compact detail.
- Do not add backend work, new dependencies, hero content, share/free-count copy, pushes, or production deployment. Commits are required after fresh verification.

### Task Dependencies
- Task 1 must land before Task 3 because the page needs the persisted view switch.
- Task 2 must land before Task 3 because the Compact row UI depends on the deterministic username helper.
- Task 3 must land before Task 4 because the responsive panel/sheet styles depend on the Compact structure.

### Exact File Map
- Modify: `frontend/src/lib/stores/dashboardPrefs.ts`
- Modify: `frontend/src/routes/+page.svelte`
- Create: `frontend/src/lib/utils/linuxUsernameInitials.ts`
- Create: `frontend/src/lib/utils/linuxUsernameInitials.test.ts`
- Create: `frontend/src/lib/components/CompactDashboard.svelte`
- Create: `frontend/src/lib/components/CompactServerRow.svelte`
- Create: `frontend/src/lib/components/CompactServerDetail.svelte`
- Create: `frontend/src/lib/styles/monitor-compact.css`

### Task 1: Persist the Default/Compact view choice and wire the menu

**Files:**
- Modify: `frontend/src/lib/stores/dashboardPrefs.ts`
- Modify: `frontend/src/routes/+page.svelte`

- [ ] **Step 1: Replace the current width-only preference with a `dashboardView` cookie store**
  - Keep the existing cookie-backed pattern.
  - Persist `default` and `compact` values only.
  - Leave the manual order store untouched.

- [ ] **Step 2: Update the header view menu to expose Default / Compact only**
  - Remove the current layout-width choice from the menu.
  - Keep theme and other existing header controls in place.
  - Make the selected view survive reloads through the cookie.

- [ ] **Step 3: Route the page to the Compact renderer when the cookie is `compact`**
  - The Default path must keep rendering the current card dashboard.
  - Network scope must continue to work in both views.

- [ ] **Step 4: Verify the page still type-checks after the preference switch**
  - Run: `cd frontend && npm run check`
  - Expected: `svelte-check` completes with no errors.

### Task 2: Add deterministic Linux-username initials and stable avatar seed logic

**Files:**
- Create: `frontend/src/lib/utils/linuxUsernameInitials.ts`
- Create: `frontend/src/lib/utils/linuxUsernameInitials.test.ts`

- [ ] **Step 1: Write the Node 24 built-in TypeScript unit test first**
  - Cover empty input, trimmed input, one username token, two-token usernames, and usernames with punctuation.
  - Assert the helper is deterministic for the same username on repeated calls.
  - Assert the helper returns uppercase 1–2 character initials and a stable seed for avatar color selection.

- [ ] **Step 2: Run the test and confirm it fails before implementation**
  - Run: `cd frontend && node --experimental-strip-types --test src/lib/utils/linuxUsernameInitials.test.ts`
  - Expected: the test runner fails because the helper does not exist yet.

- [ ] **Step 3: Implement the minimal helper**
  - Derive initials from the Linux username only.
  - Keep output stable across refreshes and platforms.
  - Export only the helper surface needed by the Compact components.

- [ ] **Step 4: Re-run the unit test and confirm it passes**
  - Run: `cd frontend && node --experimental-strip-types --test src/lib/utils/linuxUsernameInitials.test.ts`
  - Expected: all subtests pass, with `# fail 0`.

### Task 3: Build the Compact list, row, and desktop detail experience

**Files:**
- Create: `frontend/src/lib/components/CompactDashboard.svelte`
- Create: `frontend/src/lib/components/CompactServerRow.svelte`
- Create: `frontend/src/lib/components/CompactServerDetail.svelte`
- Modify: `frontend/src/routes/+page.svelte`
- Create: `frontend/src/lib/styles/monitor-compact.css`

- [ ] **Step 1: Write the Compact render path around the existing telemetry and order stores**
  - Keep rows in manual server order.
  - Keep Default rendering unchanged.
  - Select one server at a time and keep the selected row highlighted.

- [ ] **Step 2: Render each Compact row as one dense server line with exact `G#` slot labels**
  - Show GPU occupancy by slot, not by aggregate card.
  - Render 1 / 2 / 3+ user rules with initials avatars, overlapping avatars for two users, and `+N` for three or more.
  - Make the all-name tooltip/focus content expose every username on that GPU.

- [ ] **Step 3: Build the desktop detail panel for the selected row**
  - Show server identity, per-GPU utilization, and integer memory values.
  - Show all usernames without truncation.
  - Keep the panel sticky on desktop and ensure the row selection does not reorder the list.

- [ ] **Step 4: Verify the Compact UI still passes static checks**
  - Run: `cd frontend && npm run check`
  - Run: `cd frontend && npm run build`
  - Expected: both commands complete successfully with no Svelte/TypeScript errors.

### Task 4: Finish mobile bottom sheet behavior, responsive CSS, and visual QA

**Files:**
- Modify: `frontend/src/lib/components/CompactDashboard.svelte`
- Modify: `frontend/src/lib/components/CompactServerDetail.svelte`
- Modify: `frontend/src/lib/styles/monitor-compact.css`
- Modify: `frontend/src/routes/+page.svelte`

- [ ] **Step 1: Add the mobile bottom sheet with focus return**
  - Tapping a row opens the sheet.
  - The close action must restore focus to the previously selected row.
  - Escape should dismiss the sheet when it is open.

- [ ] **Step 2: Add responsive, dark/light, and reduced-motion CSS for Compact**
  - Keep the list dense and readable on desktop and mobile.
  - Respect reduced-motion preferences for panel and sheet transitions.
  - Keep the Default dashboard visuals untouched.

- [ ] **Step 3: Run the final static verification commands**
  - Run: `cd frontend && npm run check`
  - Run: `cd frontend && npm run build`
  - Expected: both commands finish cleanly.

- [ ] **Step 4: Perform visual QA in a local browser session**
  - Run: `cd frontend && npm run dev -- --host 127.0.0.1`
  - Expected: Vite serves the app locally and stays running for inspection.
  - Check Default and Compact at 1440×1000 and 390×844 in both dark and light mode.
  - Expected: no horizontal scroll, row selection stays visible, the desktop panel is sticky, the mobile sheet closes cleanly, focus returns to the selected row, and all-name tooltips/focus labels are readable.

### Self-Review Checklist
- [ ] The plan is frontend-only and defers Slack completely.
- [ ] The Default dashboard remains unchanged in behavior.
- [ ] Manual order is preserved in both views.
- [ ] The plan names every file to create or modify.
- [ ] The plan includes a Node 24 built-in TypeScript unit test for the initials helper.
- [ ] The plan includes exact verification commands and expected outcomes.
- [ ] The plan includes accessibility, responsive, and reduced-motion coverage.
- [ ] The plan includes no backend, push, or deployment work, and requires commits only after fresh verification.
