# Rose Quartz Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `Rose Quartz` as a third persistent dashboard theme without changing operational color semantics or weakening readability.

**Architecture:** Extend the existing cookie-backed theme store from a binary `dark/light` model to a three-state `dark/light/rose` model, then layer a new rose token set into the global CSS without changing component structure. The header theme control moves from a blind toggle to an explicit picker so state is visible and stable across reloads and first paint.

**Tech Stack:** SvelteKit 2, Svelte 5, TypeScript, Tailwind utility classes, global theme CSS in `app.css`, cookie-backed client stores

---

## File Map

- Modify: `frontend/src/lib/stores/theme.ts`
  Purpose: expand theme type, cookie parsing, DOM class application, and selection helpers.
- Modify: `frontend/src/routes/+page.svelte`
  Purpose: replace binary theme toggle with explicit 3-choice theme control in the header.
- Modify: `frontend/src/app.css`
  Purpose: add `html.rose` token block and rose-specific component overrides.
- Possibly modify: `frontend/src/app.html`
  Purpose: adjust initial shell class handling only if first-paint theme flash is otherwise unavoidable.

## Task 1: Theme Contract And Persistence

**Owner:** `frontend-developer`

**Files:**
- Modify: `frontend/src/lib/stores/theme.ts`

- [ ] Step 1: Reproduce the current limitation
  Check that the current theme store only supports `dark` and `light`, and that the UI toggle is binary.

- [ ] Step 2: Expand the theme type contract
  Add `rose` to the stored theme union and to cookie parsing.

- [ ] Step 3: Preserve migration behavior
  Keep existing `dark/light` cookies valid, keep unknown values falling back safely, and ensure the cookie remains `theme`.

- [ ] Step 4: Expose explicit setters
  Add a direct `setTheme('dark' | 'light' | 'rose')` helper so the UI does not rely on cycling.

- [ ] Step 5: Verify persistence logic
  Run: `npm run check`
  Expected: no type or store errors.

- [ ] Step 6: Commit
  Commit message: `feat(frontend/theme): add rose theme state`

## Task 2: Minimal Rose Shell And Safe Base Styling

**Owner:** `frontend-developer`
**Review:** `ui-designer`

**Files:**
- Modify: `frontend/src/app.css`
- Possibly modify: `frontend/src/app.html`

- [ ] Step 1: Add the `html.rose` branch
  Create a minimal rose theme shell so selecting `rose` cannot produce an unstyled or low-contrast page.

- [ ] Step 2: Establish safe base contrast
  Theme the page background, base text, surface cards, borders, and the most critical shared chrome before exposing the picker in the UI.

- [ ] Step 3: Check first-paint behavior
  If the shell always starts as `html.dark` before hydration, adjust initial class handling only as much as needed to avoid an obviously wrong flash.

- [ ] Step 4: Verify safe intermediate state
  Run: `cd /home/ircv/workspace/monitoring_v2/frontend && npm run check`
  Expected: the app compiles with the new theme branch present even before the picker is exposed.

- [ ] Step 5: Commit
  Commit message: `style(frontend/theme): scaffold rose base shell`

## Task 3: Header Theme Picker And Rose Quartz Styling

**Owner:** `frontend-developer`
**Design review:** `ui-designer`

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/app.css`

- [ ] Step 1: Replace the binary toggle control
  Change the header theme control to an explicit compact 3-state picker: `Dark`, `Light`, `Rose`.

- [ ] Step 2: Keep the control compact and keyboard-safe
  Reuse the existing header control language so the picker sits naturally beside `보기`, supports keyboard focus, and does not become visually louder than the dashboard.

- [ ] Step 3: Wire the picker to the store
  Use explicit selection instead of theme cycling.

- [ ] Step 4: Define the full rose token set
  Add the approved rose background, surface, muted surface, border, accent, and text contrast values.

- [ ] Step 5: Theme the main dashboard chrome
  Restyle page background, header, title block, tabs, view controls, and cards so the dashboard reads as `Rose Quartz` without becoming saturated.

- [ ] Step 6: Keep semantic colors stable
  Do **not** recolor online/degraded/offline, log severity, destructive actions, or other operational status colors into rose.

- [ ] Step 7: Preserve informational blue accents
  Keep dense informational accents such as GPU user names and memo author names blue unless the surrounding rose chrome makes them unreadable.

- [ ] Step 8: Theme note and panel surfaces
  Adjust memo preview, note cards, inset panels, and log neutral surfaces to match rose chrome while preserving contrast.

- [ ] Step 9: Touch forms/modals only if needed
  If server form or modal surfaces look visually broken against the new global theme shell, add the smallest matching rose treatment needed for consistency.

- [ ] Step 10: Verify styling integrity
  Run: `cd /home/ircv/workspace/monitoring_v2/frontend && npm run check`
  Run: `cd /home/ircv/workspace/monitoring_v2/frontend && npm run build`
  Expected: build succeeds, no class/type errors.

- [ ] Step 11: Manual keyboard check
  Verify the new theme picker can be reached with keyboard navigation and that focus remains visible in all three themes.

- [ ] Step 12: Commit
  Commit message: `feat(frontend/theme): expose rose theme picker and polish surfaces`

## Task 4: Visual QA And Cross-Theme Review

**Owner:** `code-reviewer`
**Support:** `ui-designer`

**Files:**
- Review only unless small follow-up adjustments are needed in:
  - `frontend/src/app.css`
  - `frontend/src/routes/+page.svelte`
  - `frontend/src/lib/stores/theme.ts`

- [ ] Step 1: Review all three themes
  Confirm `dark`, `light`, and `rose` still share the same information hierarchy.

- [ ] Step 2: Validate scan-critical areas
  Check header, tabs, server cards, GPU rows, memo preview, expanded notes, and logs.

- [ ] Step 3: Validate persistence
  Switch themes, reload, and confirm the cookie restores the selected theme.

- [ ] Step 4: Validate semantic stability
  Confirm warning/error/online semantics did not drift into rose.

- [ ] Step 5: Run final verification
  Run: `cd /home/ircv/workspace/monitoring_v2/frontend && npm run check`
  Run: `cd /home/ircv/workspace/monitoring_v2/frontend && npm run build`
  Expected: both pass on the final integrated state.

- [ ] Step 6: Commit
  Commit message: `style(frontend): polish rose quartz theme`

## Execution Notes

- This is a frontend-only change. Do not involve backend agents unless first-paint behavior unexpectedly depends on server-rendered shell logic.
- Prefer one owner per task to avoid CSS and Svelte merge conflicts.
- `ui-designer` should review visual direction, not own the final CSS write set.
- `code-reviewer` should review for readability, semantic drift, and regressions before the final polish commit.
