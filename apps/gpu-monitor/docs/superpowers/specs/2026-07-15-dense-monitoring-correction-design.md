# Dense Monitoring Dashboard Correction Design

**Date:** 2026-07-15  
**Branch:** `feature/compact-gpu-dashboard`  
**Scope:** Development server only; live repository and service remain untouched.

## Goal

Restore deterministic server reading order and make Full/Compact views dense, predictable, and focused on GPU availability without losing the existing visual hierarchy.

## Confirmed root causes

1. **Visual server order drift**
   - `$currentServers` keeps the stored/backend order.
   - The grid-based masonry action lets the browser place each next card into the earliest available grid cell.
   - Variable card heights therefore make later cards appear in a different left-to-right visual order even though the DOM array is unchanged.

2. **Full header contract conflict**
   - The zero-height collapse and detached status indicator work at wide desktop widths.
   - Compact-state `:hover` / `:focus-within` rules can re-expand the entire header, which conflicts with the intended “header gone, indicator only” state.
   - Menus and upward scroll already have explicit reveal paths, so implicit hover expansion is unnecessary.

3. **System / Memo footer imbalance**
   - Footer padding, section gaps, expanded panel separators, hardware pills, mount rows, and note cards are larger than the surrounding GPU rows.
   - Expanded System repeats large card-like containers instead of behaving like a compact telemetry inspector.

4. **Memo / soft hold fragmentation**
   - Separate Memo and advisory soft hold modes make one note composer feel like two tools.
   - Hold GPU information is concentrated in the Memo footer, separated from the GPU row it describes.

5. **Compact rows use two lines**
   - `.compact-row` uses a one-column grid at every width, so identity and slots always occupy separate implicit rows.
   - A second media rule reinforces the stacked layout below 1200px.

## Design decisions

### 1. Stable ordered masonry

Keep masonry density, but assign cards deterministically by DOM index:

- “Preserve order” means the stored/backend sequence owns a stable conceptual row: with three columns, items 1–3 always own columns 1–3, items 4–6 always own columns 1–3 next, and so on.
- Determine the resolved number of grid columns.
- Card `n` is always assigned to column `n % columnCount`; per-column stacking may produce different vertical offsets, but a card can never migrate to another column because a neighboring card is shorter.
- Stack each column independently using explicit `grid-column-start`, `grid-row-start`, and row spans.
- Recalculate on container/item resize.
- Full and Compact consume the exact same `$currentServers` sequence.
- Preserve the existing manual `serverOrder` cookie behavior.
- Remove all inline placement properties during action cleanup.

This explicitly chooses stable modulo-column placement over strict horizontal row alignment. A normal row-major grid would preserve exact y-aligned rows but reintroduce the large empty spaces the earlier masonry requirement removed.

### 2. Full scroll header

- Downward threshold collapses the header row to zero flow space.
- Only the breathing status indicator remains.
- Remove compact-state hover/focus rules that reopen the full header.
- Upward scroll, Home/PageUp/ArrowUp intent, and opening View/Management remain explicit reveal paths.
- Make the hidden header surface inert and `aria-hidden` so invisible controls cannot remain in the Tab order.
- The indicator trigger is the breathing dot itself: no 40px circular border/background container.
- Show the tiny indicator from 921px upward. At 921–1199px it occupies the normal page-edge inset; at 1200px+ it remains in the outer gutter.
- Keep the indicator detached from document flow and outside card bounds so it does not cover card content.

### 3. Dense Full footer and System inspector

Collapsed footer:

- Reduce outer padding, inter-section gap, divider padding, label size, and chevron footprint.
- Keep System and Memo as quiet one-line summaries.
- Avoid large hover blocks.
- Target values: footer gap `0.28rem`; padding no more than `0.5rem 0.75rem 0.55rem`; second-section top padding no more than `0.3rem`; labels `0.66rem`; previews `0.68rem`.

Expanded System:

- CPU/RAM/Disk stay single compact metric rows with thinner bars.
- GPU hardware becomes a dense inline matrix: `G0 · 42° · 118W`.
- Storage mounts become compact table-like rows rather than rounded subcards.
- Reduce section padding, gaps, radii, and typography while keeping tabular numeric alignment.
- Do not remove information.
- Target values: panel/section top padding no more than `0.45rem`; metric stack gap no more than `0.32rem`; hardware and mount item vertical padding no more than `0.3rem`; item radius no more than `0.5rem`; secondary text no more than `0.7rem`.

### 4. Unified Memo + optional GPU hold composer

- Remove the Memo / advisory soft hold mode switch.
- One composer always creates a note.
- GPU chips are an optional attachment:
  - no selected GPU → `memo`
  - one or more selected GPUs → `hold`
- When GPUs are selected, show one concise advisory sentence; do not repeat verbose English labels.
- Preserve backend schema and advisory semantics.
- After creation, clear content and GPU selection as today.

### 5. Hold cue on the affected GPU

- Derive active, unexpired hold notes by GPU index inside `ServerCard`.
- Pass the relevant holds into each `GpuBar`.
- Render a restrained `HOLD` marker beside the GPU user/idle line with owner and remaining time available in accessible text/title.
- Keep the expanded Memo history as the authoritative detail/delete surface.
- Replace verbose `advisory soft hold` text in previews/history with a compact semantic marker and inline GPU references.
- Keep the cue advisory: it must not change telemetry availability, utilization, or active-user calculations.

### 6. One-line Compact server rows

Desktop/tablet:

- Use two grid columns: fixed compact identity column + flexible GPU slot column.
- Keep every server on one visual row.
- Reduce row and slot height slightly.
- GPU slots may internally auto-fit but the server item itself does not become a second line.
- Exact breakpoint: `min-width: 768px`.
- Target values: identity column `minmax(7rem, 8.5rem)`; row min-height no more than `2.7rem`; slot height no more than `1.8rem`.

Narrow mobile (`max-width: 767px`):

- Permit stacking only where one line cannot remain legible.
- Preserve no-horizontal-scroll behavior.

## Accessibility

- Maintain semantic buttons, `aria-expanded`, `aria-pressed`, and accessible GPU labels.
- Hold cues include text, not color alone.
- Keep keyboard header reveal and focus-visible styles.
- Preserve reduced-motion behavior.

## Verification

Automated regression contracts will cover:

1. Ordered masonry explicitly assigns deterministic columns.
2. Header compact state has no full-header hover/focus re-expansion.
3. Footer/System density values stay within the compact contract.
4. The composer derives note kind from GPU selection and has no mode toggle.
5. GPU rows receive and render active hold cues.
6. Compact desktop/tablet rows use two columns and only narrow mobile stacks.
7. Existing contracts are deliberately updated: remove the old one-column Compact assertion, old kind-toggle assertions, and verbose advisory-copy assertions.

Browser QA at 1440×900 and 1024×768 will verify:

- expected server order,
- Full scroll collapse + dot-only indicator at both widths,
- compact System expansion,
- unified Memo/Hold interaction,
- hold cue location,
- one-line Compact rows,
- no horizontal page scroll.

Keyboard QA will verify that collapsed header controls are not tabbable, the indicator remains focusable, and upward keyboard intent restores the full header.
