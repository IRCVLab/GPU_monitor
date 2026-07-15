# Continuous Refresh Satellite Design

## Goal

Make the refresh indicator communicate a stable ten-second cadence without coupling its motion to network response timing, and remove the excessive solid accent area from Compact GPU rows.

## Refresh Indicator

- The center health dot breathes continuously at a restrained pace.
- A small satellite bead travels around a faint circular track once every `10_000ms` with linear, infinite motion.
- A fixed marker at twelve o'clock establishes the cycle boundary, so the bead position is readable as cadence rather than decorative motion.
- The animation is never keyed to a response and never restarts after data arrives.
- The same visual remains mounted in the expanded header and collapsed indicator.
- Normal health uses the health color; delayed/disconnected health uses the attention color. Text remains available through the surrounding status label and accessible button label.
- Normal and ordinary in-flight states render no visible status copy beside the ring. A warning label appears only after two consecutive refresh failures or a connection that remains stale for at least two full cadence cycles, preventing header-width shifts during routine polling.

## Polling Cadence

- The first periodic refresh command is sent `1_000ms` before the first visual cycle completes.
- Later refresh commands are sent every `10_000ms`, independent of prior response completion.
- A response updates the dashboard whenever it arrives; it does not reset or delay the visual cycle.
- If a prior request is still active at a cadence tick, that tick does not create an overlapping duplicate request. The next fixed cadence tick remains scheduled.
- Initial page loading remains immediate and separate from the periodic cadence.
- The runtime starts through the Svelte 5 effect lifecycle and returns a destroy cleanup that removes cadence timers, ticker timers, animation frames, subscriptions, and window/document listeners.

## Compact GPU Surfaces

- **Occupied:** a restrained accent-tinted surface, accent label, and user identity. It must feel occupied without becoming a solid accent block.
- **Available:** a dark empty slot with a thin accent outline and no accent-filled background.
- **Unknown/stale:** neutral muted surface and border.
- Full and Compact continue to consume the same `available | occupied | unknown` state model.
- Utilization and memory chart colors remain measurement colors and are not changed by this task.

## Collapsed Indicator Placement

- At every viewport width, the visible ring, satellite, and stroke remain inside the browser viewport.
- At desktop and tablet widths, the indicator occupies the page gutter without overlapping the first card.
- At mobile widths, the visible ring scales to fit the `16px` gutter while the invisible hit target remains at least `24px`.
- The expanded detail panel begins at the viewport edge and remains fully on screen.

## Validation

- Contract tests prove the satellite uses a fixed `10s` infinite linear cycle and that response state cannot restart it.
- Contract tests prove transient `갱신 중`/`동기화` copy is absent and warning copy is gated by persistent failure thresholds.
- Scheduling tests prove the next cadence is arranged before a refresh request executes, response completion does not schedule animation state, and page runtime cleanup is registered through the Svelte lifecycle.
- Compact CSS tests prove occupied uses a tint rather than a solid fill.
- Browser QA at `1440px`, `900px`, and `390px` verifies viewport containment, no card overlap, no horizontal overflow, continuous satellite motion, and fixed request cadence.
