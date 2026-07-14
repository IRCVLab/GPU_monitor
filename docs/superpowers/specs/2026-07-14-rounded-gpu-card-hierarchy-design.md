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

Preserve the rounded outer server-card shell, but make the inside feel less like a grid of boxes. The card should read as one calm object whose strongest decision signals are server identity, GPU model, VRAM, and a natural visual sense of free capacity.

Availability must be perceived visually, not narrated as visible count text. Replace visible labels such as `3 available` with an unboxed per-GPU circular availability cue: one small dot per GPU. Free GPUs use the semantic availability/accent color. Occupied or shared GPUs use a quiet neutral. The cue lets researchers count capacity at a glance without adding another badge, pill, or summary metric.

## Visual hierarchy

1. Server name and health remain the top-level anchor.
2. GPU model and VRAM move up near the header or top of the GPU section, above telemetry details.
3. The per-GPU availability dots sit with model/VRAM as a compact visual capacity cue.
4. Usernames are the primary per-GPU occupancy detail and remain fully visible.
5. Utilization, memory use, temperature, power, system metrics, storage, and notes are secondary details.

## Required changes for implementation

### Server card shell

- Preserve the rounded outer card radius and soft elevation.
- Keep cards as the default dashboard surface; do not replace them with a compact availability table.
- Remove interior treatments that make the card look like multiple boxed widgets.
- Keep server health, host/IP, refreshed time, network-in-All-scope behavior, and edit/manage access.
- Do not add visible textual availability counts such as `3 available`, `3 free`, or `free 3/8`.

### Model, VRAM, and availability cue

- Promote GPU model and VRAM to the upper hierarchy of each card.
- Show VRAM as integer GB.
- Add one small circular dot per GPU.
- Dot states:
  - Free GPU: semantic availability/accent color.
  - Occupied GPU: quiet neutral.
  - Shared GPU: quiet neutral; shared ownership remains visible in the username row.
  - Unknown/stale GPU data: quiet neutral or muted outline, paired with existing server freshness/status context.
- Dots are unboxed: no pill container, no chip border, and no table/grid cell.
- Dots may have accessible labels/tooltips, but the visible card must not include count text.

### GPU rows

- Remove boxed `G#` labels. If the GPU index remains visible, make it inline and quiet, not a badge.
- Remove boxy or grid-like row containers.
- Keep all usernames visible, wrapping as needed; do not truncate, collapse, or hide shared-user names.
- Keep utilization and memory available, but reduce them below user/model/availability hierarchy.
- Do not present utilization and memory as two equal-strength competing bars.
- Keep integer memory labels where memory text is shown.

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
- Interior boxed panels, grid-cell styling, excessive dividers, and stacked glass/box effects are removed or visually quieted.
- Model and VRAM are easier to notice than telemetry bars.
- Availability is communicated by the per-GPU dot set, not by visible count text.

### Availability dots

- Each GPU maps to exactly one visible dot.
- Free dots use semantic availability/accent color.
- Occupied and shared dots are quiet neutral.
- The dot group has no enclosing pill, chip, table cell, or box.
- No visible `3 available`-style count appears in the card header, body, or footer.
- Screen-reader text may summarize counts for accessibility, but it must not become visible UI copy.

### Usernames and occupancy

- Every username from every GPU remains visible by default.
- Shared GPUs show all users, wrapping without ellipsis.
- Occupied/shared state is understandable from usernames plus quiet dot state.

### Telemetry

- Utilization, memory use, temperature, power, system metrics, and storage remain accessible.
- Utilization and memory no longer appear as equal-strength dual bars that compete with availability and usernames.
- VRAM and memory values are formatted as integer GB.

### System and notes

- Existing system disclosure behavior remains available.
- Existing notes preview, expansion, creation, expiry display, password delete, loading, and error states remain available.
- Footer areas are visually quiet and unboxed.

### Responsive and accessible behavior

- The design works at desktop, tablet, and mobile widths without hiding usernames.
- Keyboard access remains for edit/manage actions, system disclosure, notes disclosure, note form fields, and delete actions.
- Color is not the only accessibility channel: visible usernames, server health text, and accessible labels preserve meaning.
- Reduced-motion preferences are respected for hover, expansion, bar, and dot animations if any are added.

## Verification checklist for later implementation

- Run Svelte diagnostics and production build.
- Inspect dark and light desktop screenshots around 1440px width.
- Inspect dark and light mobile screenshots around 390px width.
- Confirm no frontend source changed during this documentation-only step.
- Confirm `git diff --name-only` contains only documentation files for this step.
