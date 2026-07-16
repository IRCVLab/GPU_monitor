# Pressure-Aware System Telemetry, Stable Masonry, and Motion Design

Date: 2026-07-16
Status: approved by direct user requirements
Target: `/home/ircv/workspace/monitoring_v2_dev`
Branch: `feature/compact-gpu-dashboard`

## Outcome

The dashboard must answer two questions without requiring the researcher to interpret device-specific throughput:

1. What is the server's overall load relative to its logical CPU capacity?
2. If pressure exists, is work waiting because CPU capacity is contended or because tasks are stalled on I/O?

It must also keep user-defined server order visually understandable while cards expand, remove ambiguous failure overlays, shorten hold expiry copy, and reduce avoidable client work without changing the 10-second monitoring contract.

## Non-goals

- Do not infer storage saturation from MB/s or render MB/s in the UI.
- Do not add an agent or daemon to GPU servers.
- Do not add another SSH command or shorten the 10-second collector cadence.
- Do not remove the existing fixed 10-second frontend refresh request/animation contract.
- Do not modify or restart the live checkout or live services.
- Do not reorder servers from telemetry, availability, height, or status.

## Evidence and semantic source

Linux PSI is the primary pressure signal.

- `/proc/pressure/cpu` `some avg10`: percentage of recent time where at least one runnable task was stalled waiting for CPU.
- `/proc/pressure/io` `some avg10`: percentage of recent time where at least one task was stalled on I/O.
- `/proc/pressure/io` `full avg10`: percentage of recent time where all non-idle tasks were stalled on I/O.
- System-wide CPU `full` is undefined and exported as zero; it must not be used.
- MB/s is throughput, not a bottleneck signal. It remains supporting detail only.

Official references:

- https://docs.kernel.org/accounting/psi.html
- https://docs.kernel.org/filesystems/proc.html
- https://docs.kernel.org/admin-guide/cpu-load.html
- https://docs.kernel.org/admin-guide/iostats.html

The UI thresholds below are product heuristics, not kernel ABI:

| PSI avg10 | UI state | Meaning |
| --- | --- | --- |
| missing | unknown | telemetry unavailable |
| < 5% | 여유 | low observed stall |
| >= 5% and < 20% | 압박 | meaningful observed stall |
| >= 20% | 병목 | high observed stall that needs attention |

The tooltip must say that this is a recent 10-second stall ratio, not absolute hardware utilization.

## Approach decision

### Rejected: MB/s-based busy classification

A high-capacity NVMe/RAID server can be healthy at a high MB/s value, while a latency-bound workload can be stalled at a low MB/s value. Device count and parallelism make a single throughput threshold misleading.

### Chosen: load-average summary with PSI cause diagnosis

Read CPU PSI, the existing I/O PSI, 1/5/15 load averages, and logical CPU count in the same current remote Python command. Reuse the already-read `/proc/stat` to expose `procs_running`. Keep backend disk R/W rates only for API compatibility; the frontend does not render them.

### Rejected: per-server monitoring daemon

A daemon would add deployment, lifecycle, and failure surface to every GPU server. Procfs reads inside the existing 10-second SSH command are sufficient and materially cheaper operationally.

## Telemetry contract

Append optional fields to the existing CSV payload so old 3-, 6-, 10-, and 12-field parsers remain compatible:

- `cpu_pressure_some`: `/proc/pressure/cpu` `some avg10`
- `cpu_running_tasks`: `procs_running` from `/proc/stat`
- `load_avg_1`, `load_avg_5`, `load_avg_15`: `os.getloadavg()`
- `cpu_count`: logical CPU count from `os.cpu_count()`

Do not collect CPU `full`.

Expose optional frontend fields:

- `cpu_pressure_some: number | null`
- `cpu_running_tasks: number | null`
- `load_avg_1: number | null`
- `load_avg_5: number | null`
- `load_avg_15: number | null`
- `cpu_count: number | null`

The collector continues to execute one system command per normal collection cycle. The existing disk counters, I/O PSI, PCI inventory, CPU utilization sample, and 10-second cadence remain unchanged, and the disk-rate API fields stay backward-compatible even though the UI no longer renders MB/s.

## System UI hierarchy

### Collapsed row

Keep one compact baseline while preserving the machine resources users expect to scan first.

- Visual order: `CPU 28% · RAM 42% · Storage 63% · 부하 3.2 / 32`.
- CPU, RAM, and Storage use percentage-only summaries. Absolute RAM and Storage capacities stay in the expanded panel.
- Load remains a trailing diagnostic:
  - numerator is raw `load_avg_1`;
  - denominator is logical `cpu_count`;
  - normalized ratio (`load_avg_1 / cpu_count`) drives visual/state logic only;
  - normal load stays lower contrast, while pressure or bottleneck states gain semantic emphasis.
- Do not reserve collapsed-row width for a load gauge. On narrow cards, preserve CPU/RAM/Storage and allow trailing load/cause context to clip first.
- Historical/offline telemetry keeps neutral `–` placeholders.
- GPU-only degradation with current telemetry keeps the current resource summary and load context.

### Expanded row

Keep dense, tabular facts.

- 1/5/15 load averages
- logical CPU count
- CPU utilization
- CPU stall `some avg10`
- runnable task count
- I/O stall `some/full avg10`
- blocked task count
- existing RAM, GPU power, disk capacity, mounts

Expanded historical values remain under the `마지막 수집값` label. Backend disk-rate fields remain compatible for non-UI consumers, but the frontend does not render MB/s.

## Stable Masonry

### Initial placement

The allocator maintains DOM/user order and non-decreasing visual start rows.

For each item:

1. Find the current shortest column row.
2. Treat every column within one masonry row of that minimum as effectively similar.
3. Choose the leftmost effectively-similar column.
4. Apply the existing monotonic start-row floor.
5. Store the assigned column by element identity.

This makes similar columns prefer the first/left column without ignoring large height differences.

### Height-only reflow

On `ResizeObserver` height changes such as System or Memo expand/collapse:

- preserve each card's assigned column;
- recompute only row starts/spans within those columns;
- animate resulting FLIP deltas;
- horizontal delta must remain zero.

Cards may move vertically but must not jump between columns.

### Structural reflow

Clear and recompute column assignments only when:

- masonry is newly enabled;
- column count changes;
- child identity/order changes because of network filtering, drag reorder, insertion, or deletion.

The observer caches measured item heights and coalesces work through one RAF. It must not clear and rewrite all placement styles merely to measure every animation frame.

## Failure veil

Default impaired state:

- original information remains recognizable through a light body blur;
- a translucent semantic veil shows the failure label and supporting reason/age;
- server identity header remains sharp.

Hover or focus-within:

- body becomes fully sharp and opaque;
- veil, veil text, and veil backdrop blur transition to `opacity: 0` and `visibility: hidden`;
- no failure text remains over the raw card content;
- the sharp header status cue remains, so the problem state is not lost.

Use opacity/transform for the veil transition. Keep filter animation restrained and cover reduced motion.

## Expiry copy

Remove the word `남음` from active relative expiry everywhere:

- `42초`
- `12분`
- `7시간`
- `3일`

Keep `만료됨` for expired notes.

Apply this consistently to Full cards, Compact hold hints, and NoteForm.

## Motion system

Use restrained native-feeling motion:

- card/disclosure/veil controls: approximately 240-280ms;
- layout FLIP: approximately 380-420ms;
- GPU metric width changes: approximately 320ms;
- easing: `cubic-bezier(0.22, 1, 0.36, 1)` for settling motion;
- no animated layout properties where transform/opacity can express the state;
- all motion remains disabled or effectively immediate for `prefers-reduced-motion`.

The view toggle animation remains short and must not become ornamental.

## Bounded performance optimization

### Chosen

- Pass the page's existing visibility-aware `nowMs` clock into every Full `ServerCard`.
- Remove the per-card one-second intervals.
- Keep NoteForm's timer gated to an open note composer.
- Cache Masonry column assignment and observed heights.
- Keep one RAF scheduling gate for layout.
- Preserve identity-based store equality so unchanged server snapshots do not rerender.

### Explicitly not changed

The frontend 10-second HTTP refresh is retained even while WebSocket is connected. The user explicitly requires the update request and visual cadence to continue independently every 10 seconds, so WS-only transport is out of scope.

## Accessibility

- Pressure status is communicated by text and color, never color alone.
- Pressure help text explains PSI semantics.
- Failure reveal also works with keyboard focus.
- Hidden veil uses visibility and pointer behavior that does not trap focus.
- Reduced motion remains functional.
- No new horizontal overflow at 390px.

## Verification

Automated:

- backend parser compatibility for 3/6/10/12 fields;
- system command includes CPU PSI and running tasks without another command;
- frontend normalization/equality covers new fields;
- pressure classifier boundary tests at missing, 4.9, 5, 19.9, and 20;
- collapsed CPU/I/O copy contract;
- MB/s absent from collapsed I/O classification;
- sticky-column and left-bias allocator tests;
- structural reset and height-only assignment contract;
- expiry copy has no active `남음`;
- failure veil fully hides on hover/focus;
- one shared page clock, no ServerCard interval;
- full frontend/backend suites, check, build, diff-check.

Browser:

- Grid and Masonry at 1440px and 390px;
- System expand/collapse causes no horizontal card movement;
- earlier server starts remain non-decreasing;
- similar columns prefer left;
- failure veil default and hover/focus reveal;
- CPU/I/O pressure labels and expanded raw details;
- smooth motion with no visible jump;
- no horizontal overflow;
- live checkout remains `c50f9d2`, clean and healthy.
