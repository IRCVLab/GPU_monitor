# Rounded GPU Card Hierarchy Design

## Status

Approved design direction for the default server-card dashboard and the implementation pass that follows it. This spec refines the current rounded cards only.

## Context

- Repository: `~/workspace/monitoring_v2_dev`
- Branch: `feature/apple-dashboard-refinement`
- Framework/styling: SvelteKit 5, Tailwind, repo CSS tokens in `frontend/src/app.css` and card styles in `frontend/src/lib/styles/monitor-cards.css`
- Relevant UI files for the later implementation: `frontend/src/lib/components/ServerCard.svelte`, `frontend/src/lib/components/GpuBar.svelte`, `frontend/src/lib/styles/monitor-cards.css`
- Existing design source of truth: `DESIGN.md`
- Production isolation: do not edit `~/workspace/monitoring_v2`

## Approved direction

Preserve the rounded outer server-card shell, but make the inside feel less like a grid of boxes. The card should read as one calm object whose strongest decision signals are server identity, GPU model, VRAM, a coherent GPU map, and high-contrast GPU ownership. The second pass specifically corrects the current low-contrast result where card bodies feel empty and isolated dots read as decorative/disconnected.

Availability must be perceived visually, not narrated as visible count text. Replace visible labels such as `3 available` with an unboxed per-GPU map: one small mark per GPU. Free GPUs use a filled semantic availability/accent mark. Occupied or shared GPUs use a thin quiet neutral ring. The cue lets researchers count capacity at a glance without adding another badge, pill, or summary metric. Cards with at least one free GPU get a subtle theme-color upper wash/accent; full cards remain neutral.

## Visual hierarchy

1. Server name and health remain the top-level anchor.
2. GPU model and VRAM move up near the header or top of the GPU section, above telemetry details.
3. The per-GPU GPU map sits with model/VRAM as a compact visual capacity cue: free is filled accent, occupied/shared is a thin quiet ring.
4. Usernames are the primary per-GPU occupancy detail, regain primary contrast, and remain fully visible.
5. Occupied/shared rows keep exactly one thin utilization track; memory is integer text only. Temperature, power, system metrics, storage, host, timestamps, and notes are secondary details.

## Required changes for implementation

### Server card shell

- Preserve the rounded outer card radius and soft elevation.
- Keep cards as the default dashboard surface; do not replace them with a compact availability table.
- Remove interior treatments that make the card look like multiple boxed widgets or empty low-contrast bodies.
- Add a subtle theme-color upper wash/accent only to cards with at least one free GPU; full cards stay neutral.
- Keep server health, host/IP, refreshed time, network-in-All-scope behavior, and edit/manage access.
- Do not add visible textual availability counts such as `3 available`, `3 free`, or `free 3/8`.

### Model, VRAM, and GPU map

- Promote GPU model and VRAM to the upper hierarchy of each card.
- Show VRAM as integer GB.
- Add one small circular mark per GPU as one coherent GPU map, not separated decorative dots.
- Map states:
  - Free GPU: filled semantic availability/accent mark.
  - Occupied GPU: thin quiet neutral ring.
  - Shared GPU: thin quiet neutral ring; shared ownership remains visible in the username row.
  - Unknown/stale GPU data: quiet muted ring or outline, paired with existing server freshness/status context.
- The GPU map is unboxed: no pill container, no chip border, and no table/grid cell.
- Map marks may have accessible labels/tooltips, but the visible card must not include count text.

### GPU rows

- Remove boxed `G#` labels. If the GPU index remains visible, make it inline and quiet, not a badge.
- Remove boxy or grid-like row containers.
- Keep all usernames visible at primary contrast, wrapping as needed; do not truncate, collapse, or hide shared-user names.
- Free rows use accent `사용 가능` and a borderless subtle gradient/tonal field, not a box.
- Keep utilization and memory available, but reduce them below user/model/GPU-map hierarchy.
- Restore exactly one thin utilization track per occupied/shared row.
- Do not present utilization and memory as two equal-strength competing bars; memory is integer text only.
- Keep host, timestamps, system metrics, storage, and notes secondary.

### Footer, system, and notes

- Keep system telemetry, storage details, notes preview, note expansion, note creation, and note deletion functionality.
- Remove boxed footer sections and excessive dividers.
- Treat footer content as a quiet continuation/disclosure area of the same rounded card.
- Notes must still wrap naturally and preserve existing expiry/user/delete interactions.

## Non-goals

- Do not modify backend collectors, WebSocket/polling behavior, notes APIs, authentication, or server CRUD semantics.
- Do not implement a compact availability board in this pass.
- Do not introduce new frontend dependencies unless separately approved.
- Do not edit production path `~/workspace/monitoring_v2`.

## Acceptance criteria

### Documentation and scope

- `DESIGN.md` and this spec agree that visible textual availability counts are rejected.
- The implementation plan references this spec before editing frontend source.
- Only the development repository path is used.

### Card hierarchy

- Rounded outer server cards remain visually intact.
- Interior boxed panels, grid-cell styling, excessive dividers, stacked glass/box effects, and empty low-contrast bodies are removed or visually corrected.
- Model and VRAM are easier to notice than telemetry bars.
- Cards with at least one free GPU have a subtle theme-color upper wash/accent; full cards remain neutral.
- Availability is communicated by the per-GPU GPU map, not by visible count text.

### GPU map

- Each GPU maps to exactly one visible mark.
- Free marks are filled semantic availability/accent.
- Occupied and shared marks are thin quiet neutral rings.
- The marks read as one coherent GPU map, not decorative/disconnected dots.
- The GPU map has no enclosing pill, chip, table cell, or box.
- No visible `3 available`-style count appears in the card header, body, or footer.
- Screen-reader text may summarize counts for accessibility, but it must not become visible UI copy.

### Usernames and occupancy

- Every username from every GPU remains visible by default at primary contrast.
- Shared GPUs show all users, wrapping without ellipsis.
- Occupied/shared state is understandable from usernames plus quiet ring state.
- Free rows use accent `사용 가능` and a borderless subtle gradient/tonal field, not a boxed empty row.

### Telemetry

- Utilization, memory use, temperature, power, system metrics, and storage remain accessible.
- Occupied/shared rows show exactly one thin utilization track.
- Utilization and memory never appear as equal-strength dual bars that compete with availability and usernames.
- VRAM and memory values are formatted as integer GB; memory is text only.

### System and notes

- Existing system disclosure behavior remains available.
- Existing notes preview, expansion, creation, expiry display, password delete, loading, and error states remain available.
- Footer areas are visually quiet and unboxed.

### Responsive and accessible behavior

- The design works at desktop, tablet, and mobile widths without hiding usernames.
- Keyboard access remains for edit/manage actions, system disclosure, notes disclosure, note form fields, and delete actions.
- Color is not the only accessibility channel: visible usernames, `사용 가능` text, server health text, GPU-map accessible labels, and ring/fill shape differences preserve meaning.
- Reduced-motion preferences are respected for hover, expansion, bar, and GPU-map animations if any are added.

## Verification checklist for later implementation

- Run Svelte diagnostics and production build.
- Self-review that the result has no equal dual bars: occupied/shared rows may have exactly one thin utilization track, while memory remains integer text only.
- Inspect dark and light desktop screenshots around 1440px width.
- Inspect dark and light mobile screenshots around 390px width.
- Confirm no frontend source changed during this documentation-only step.
- Confirm `git diff --name-only` contains only documentation files for this step.
