# Dashboard Trust and Density Polish Design

**Date:** 2026-07-15  
**Branch:** `feature/compact-gpu-dashboard`  
**Scope:** Development repository only. The live repository and the restored public service on port 5173 are not modified.

## Goal

Make the dashboard trustworthy at a glance: server order must never change locally, the compact scroll state must always retain its status indicator, GPU memory telemetry must use the available width, and secondary System/Memo controls must remain dense without looking unfinished.

## Root causes

1. Server order is intentionally user-controlled. Removing the `serverOrder` overlay also removed drag reordering, which is a required interaction. The correct contract is backend order as the initial fallback and the saved user order after manual movement.
2. `.ops-indicator-anchor` is forced to `display: none` at `max-width: 920px`, even when the header state says the indicator is visible.
3. GPU metrics split the row into two equal columns, while the memory value reserves `10ch`. That reserve leaves the Mem track shorter than Util and wastes horizontal space.
4. The visible header repeats exact relative and next-refresh seconds. The text is noisy, wraps more easily, and leaves almost no bottom breathing room.
5. Expanded Memo content is one undifferentiated stack; collapsed System/Memo rows rely on text plus a glyph chevron without a strong but quiet structural cue.

## Design decisions

### Server order

- Sort by `display_order`, then `server_id` as the deterministic initial order.
- Preserve drag reordering and the saved `serverOrder` overlay.
- Full and Compact consume the same ordered store so switching views never changes the sequence.
- Changing card layout never resets or reinterprets the saved order.

### Card layout choice

- Add `그리드` and `빈틈 없이` under `보기 > 배치`.
- `그리드` uses normal row-major CSS Grid and permits row whitespace when card heights differ.
- `빈틈 없이` enables the existing deterministic ordered masonry placement.
- Persist this independently from the Full/Compact view preference.

### Scroll header and health cadence

- A downward scroll still collapses the header to zero flow height.
- The breathing status dot remains available at every viewport width, including 390px and 900px.
- The full header shows only the semantic state (`정상`, `확인 중`, `동기화`, `지연`) plus a compact refresh-cadence track.
- Exact last-refresh and next-refresh text remains in the status control title/accessible label and the expanded indicator panel, not in the persistent header line.
- The status line receives at least `0.5rem` visual space beneath it before the header border.

### GPU metric geometry

- Keep `Util`, `Mem`, `%`, and `GB` labels.
- Allocate more of the metric row to Mem than Util.
- Remove the artificial `10ch` memory-value reservation and reserve only the width required for realistic values.
- At the default 22rem card width, the Mem track must be wider than the Util track and must fill the newly available space instead of creating a blank right-side region.

### System and Memo controls

- Collapsed rows use a subtle section marker, label, one-line summary, and CSS-drawn disclosure angle.
- Expanded System keeps every data field but retains the compact metric/table treatment.
- Expanded Memo is divided into `기록` and `작성` groups. Existing notes remain first; the composer remains second.
- Empty note history has a deliberate compact empty state instead of an unexplained gap.
- HOLD remains integrated with Memo and remains visible on affected GPU rows.

## Acceptance criteria

1. Dragging a Full card updates the saved user order again.
2. Full and Compact render the same saved sequence, with backend order as the fallback.
3. `보기 > 배치` exposes `그리드` and `빈틈 없이`, and switching either does not alter the sequence.
4. Compact-scroll indicator is visible at 390, 900, 1024, and 1440px without covering card content.
5. Persistent full-header status contains no visible `n초 전` or `n초 뒤` text; those details remain accessible.
6. At 1440px, the Mem track is wider than Util while `used/totalGB` remains visible.
7. Memo expansion visibly separates history from composer and preserves note deletion and hold creation.
8. Existing frontend tests, `npm run check`, `npm run build`, and backend tests pass.
