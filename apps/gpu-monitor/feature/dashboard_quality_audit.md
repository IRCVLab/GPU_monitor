# Dashboard Quality Audit

Date: 2026-03-23

## Scope

Frontend quality audit focused on the current dashboard state after the recent zoom/layout/theme changes.

Audited files:

- `frontend/src/routes/+page.svelte`
- `frontend/src/app.css`
- `frontend/src/lib/components/GpuBar.svelte`
- `frontend/src/lib/components/ServerCard.svelte`
- `frontend/src/lib/stores/theme.ts`
- `frontend/src/lib/stores/dashboardPrefs.ts`

Parallel reviewers:

- `code-reviewer`
- `ui-designer`
- `architect-reviewer`

Local quality checks:

- `cd frontend && npm run check` ✅
- `cd frontend && npm run build` ✅

## Status After Remediation Pass

This document records the initial audit snapshot that drove the current fix pass.

Current status after the 2026-03-23 remediation work:

- Finding 1: addressed by rebuilding the GPU row so usernames become the primary readable lane
- Finding 2: addressed by replacing breakpoint-only grid density with container-aware auto-fit sizing
- Finding 3: addressed by adding `dark | light | rose` theme support and a real theme picker
- Finding 4: addressed in this pass by raising the dark theme contrast floor and simplifying surface separation
- Finding 5: addressed by giving the collapsed system preview explicit truncation ownership
- Finding 6: partially improved, but the broad theme override structure in `app.css` still remains a longer-term maintenance risk

Use the findings below as the original root-cause record, not as a claim that all listed problems are still present in the latest working tree.

## Findings

### 1. High: GPU user names are still starved of width

Files:

- `frontend/src/lib/components/GpuBar.svelte`
- `frontend/src/app.css`

Root cause:

- The GPU row hard-reserves most horizontal width to fixed utility/memory lanes.
- The user lane is the only flexible region.
- The user lane then gets clipped again by per-token ellipsis and a hard `max-width`.
- Only the first two users are rendered.

User impact:

- The most important occupancy cue, `who is using this GPU`, becomes unreadable first.
- On normal desktop widths this already truncates, not only on edge cases.

Required fix direction:

- Rebuild the GPU row around readable user identity first.
- Stop clipping each username token independently.
- Prefer a layout that gives users a full-width or two-line region, with metrics compressing before user names disappear.

Owner:

- `frontend-developer`

### 2. High: `폭=전체` + `배율=기본` is structurally inconsistent

Files:

- `frontend/src/routes/+page.svelte`
- `frontend/src/app.css`
- `frontend/src/lib/stores/dashboardPrefs.ts`

Root cause:

- `CSS zoom` is being used to mimic browser zoom.
- Tailwind/media-query breakpoints do not change under `zoom`.
- The page also shrinks the viewport wrapper width while separately increasing grid density in `full` mode.
- Result: cards become visually too narrow while still keeping 3 to 4 columns.

User impact:

- On narrower monitors, a large portion of GPU card content becomes unreadable.
- The UI looks like “zoomed in content packed into an unzoomed grid”.

Required fix direction:

- Stop using viewport breakpoints alone to decide card density.
- Base the server grid on a minimum readable card width.
- Keep `width` and `scale` orthogonal: width decides shell usage, scale decides visual scale, but neither should force unreadable card widths.

Owner:

- `main`

### 3. High: Pink mode is not implemented in production code

Files:

- `frontend/src/lib/stores/theme.ts`
- `frontend/src/routes/+page.svelte`
- `frontend/src/app.css`
- `frontend/src/app.html`

Root cause:

- Theme state only supports `dark | light`.
- The UI still uses a binary toggle.
- CSS only defines `html.dark` and `html.light`.
- Any non-`light` cookie value is effectively forced back to `dark`.

User impact:

- `Rose Quartz` does not exist in the running app.
- A previously stored third-theme cookie would be lost on load.

Reference spec:

- `feature/pink_theme.md`

Required fix direction:

- Expand the theme model to `dark | light | rose`.
- Replace the binary toggle with an explicit 3-way selector.
- Add a real `html.rose` theme branch.
- Handle first paint so users do not flash back to dark before hydration.

Owner:

- `frontend-developer`

### 4. Medium-High: Dark theme contrast floor is still too low on weak displays

Files:

- `frontend/src/app.css`
- `frontend/src/lib/components/ServerCard.svelte`
- `frontend/src/lib/components/GpuBar.svelte`

Root cause:

- Surface layers are mostly separated by small white-alpha changes.
- Secondary text frequently stays in a low-contrast range.
- The theme uses many gradients/highlights but not enough actual luminance separation.

User impact:

- Cards, panels, toggles, and metadata flatten together on low-quality monitors.
- The theme mood is present, but the detail and hierarchy do not survive poor contrast conditions.

Preferred direction:

- Use the `Porcelain Graphite` direction selected in visual review.
- Raise the contrast floor for dark surfaces and secondary text.
- Reduce decorative gradient stacking and improve material separation with cleaner luminance steps and hairlines.

Owner:

- `main`

### 5. Medium: System preview row can crowd or overflow on narrow cards

Files:

- `frontend/src/lib/components/ServerCard.svelte`

Root cause:

- The collapsed system preview is an inline flex row without an explicit truncation/min-width strategy.

User impact:

- Once card widths shrink, the preview competes badly with the toggle row and other metadata.

Required fix direction:

- Give the preview text a proper collapse rule.
- Ensure the collapsed system summary degrades gracefully on narrow cards.

Owner:

- `frontend-developer`

### 6. Medium: Theme handling is brittle

Files:

- `frontend/src/app.css`
- `frontend/src/lib/stores/theme.ts`

Root cause:

- Theme styling relies heavily on broad substring selectors with `!important`.
- Theme application is mostly client-side after load.

User impact:

- Small utility class changes can cause theme regressions.
- Wrong-theme first paint remains a likely source of polish issues.

Required fix direction:

- Keep the current CSS structure for now, but tighten theme state handling and initial class application.
- Avoid widening the brittle pattern further than necessary during this pass.

Owner:

- `frontend-developer`

## Implementation Split

### Track A: Theme System And Rose Quartz

Owner:

- `frontend-developer`

Files:

- `frontend/src/lib/stores/theme.ts`
- `frontend/src/app.html`

Responsibilities:

- Add `rose` theme support in store/cookie logic
- Preserve existing dark/light users
- Support first-paint theme application

### Track B: GPU Row And Card Content Readability

Owner:

- `frontend-developer`

Files:

- `frontend/src/lib/components/GpuBar.svelte`
- `frontend/src/lib/components/ServerCard.svelte`

Responsibilities:

- Rebuild the GPU row so user identity remains readable
- Fix collapsed system preview degradation

### Track C: Responsive Grid, Header Theme Picker, Dark Theme Detail

Owner:

- `main`

Files:

- `frontend/src/routes/+page.svelte`
- `frontend/src/app.css`

Responsibilities:

- Replace breakpoint-only card density with readable container behavior
- Introduce explicit 3-way theme selection in header
- Implement `Porcelain Graphite` contrast/detail improvements
- Integrate `Rose Quartz` shell styling with stable semantics

## Final Review Requirements

- `cd frontend && npm run check`
- `cd frontend && npm run build`
- code-reviewer pass on integrated diff
- manual validation in:
  - `폭=전체` + `배율=기본`
  - narrower monitor widths
  - `dark / light / rose`
