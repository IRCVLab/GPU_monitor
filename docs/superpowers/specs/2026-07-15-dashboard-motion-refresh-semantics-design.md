# Dashboard Motion and Refresh Semantics Design

## Goal

Make layout changes, scroll-state feedback, refresh timing, and GPU color meaning read as one coherent monitoring system rather than unrelated effects.

## Decisions

### 1. One GPU color language

- **Available:** green. This is the primary resource-acquisition cue and must mean the same thing in Full and Compact.
- **Occupied:** blue. Full keeps a strongly colored GPU index for active jobs, but it no longer competes with the green availability meaning.
- **Unknown/stale:** amber. It must never look available.
- Full and Compact both consume the same `getCompactGpuState(...)` result and expose the same `data-state` values.
- Utilization and memory graphs keep their independent chart colors; they describe measurements, not occupancy.

### 2. Refresh cadence is a circular state indicator

- Remove the ambiguous horizontal marquee from the expanded header.
- Use one shared visual in both expanded and collapsed states: a small center status dot surrounded by a circular progress ring.
- The ring fills linearly toward the next scheduled refresh.
- When the deadline is reached, it remains full while the HTTP refresh is in flight.
- The ring begins its next cycle only after the response has completed and the next deadline has been scheduled.
- At cycle restart, it contracts from full to empty briefly and then begins the next linear fill. There is no visible 100-to-0 snap.
- Normal uses green; delayed/disconnected uses amber. Exact relative times remain available to assistive text and the detail popover, not as constantly changing header copy.
- The animation is CSS-driven so ordinary server-state updates do not restart it.

### 3. Collapsed scroll indicator lives on the left

- Scrolling down past the existing direction threshold collapses the header and reveals the ring indicator.
- The indicator is fixed near the upper-left page gutter, not the right edge.
- Its visual footprint is only the dot and ring; the larger invisible hit target remains for accessibility.
- Hover, focus, or click opens the existing status/network detail panel to the right of the indicator.
- The hidden indicator remains mounted and visually hidden with opacity/visibility rather than `display: none`, so its ring stays synchronized with the expanded header.

### 4. Gapless layout changes use FLIP motion

- The Masonry action stores each card’s last document-space rectangle.
- Before applying a new placement it cancels stale layout animations; after placement it compares old and new rectangles.
- Cards with changed positions animate from the inverse offset to their new position using the Web Animations API.
- This applies to Grid ↔ Gapless switching and later Masonry reflows caused by card size changes.
- Motion lasts about 360ms with a restrained native-feeling easing curve.
- `prefers-reduced-motion: reduce` disables the movement without changing layout behavior.

## Refresh Data Flow

1. A refresh deadline is scheduled.
2. The ring fills linearly toward that deadline.
3. At the deadline, the ring reaches full and the request begins.
4. The ring stays full for the complete request duration.
5. Response data is merged without recreating the ring.
6. A new deadline is scheduled from the completion point/aligned interval.
7. The ring receives a new cycle key, contracts, and starts the next fill.

If a refresh is already active when another trigger arrives, the system retries shortly without scheduling a false new cadence cycle.

## Accessibility and Motion

- The ring is decorative; the surrounding button and status text carry the accessible label.
- Status is never communicated by color alone: `정상`, `지연`, `오프라인`, or `확인중` remains available in text.
- Reduced-motion users receive a static ring state and immediate layout placement.
- Focus targets remain at least 24px for the compact indicator and retain visible focus styling.

## Verification

- Contract tests prove there is no old horizontal cadence bar, both header states use the shared ring, and the floating indicator is left-aligned.
- Scheduling tests prove a new visual cycle is started only after refresh completion.
- GPU component tests prove Full and Compact consume the same availability state and semantic color selectors.
- Layout-motion tests prove document-space FLIP deltas and reduced-motion bypass.
- Browser QA verifies Grid ↔ Gapless movement, header collapse/indicator visibility, ring hold/restart behavior, no content overlap, and no horizontal overflow.

