# Resilient Telemetry and Failure-State Design

- Status: Approved for development implementation
- Date: 2026-07-16
- Surface: development dashboard only
- Live invariant: /home/ircv/workspace/monitoring_v2 remains untouched at c50f9d2

## Problem

The dashboard currently labels Linux PSI I/O pressure as a generic I/O percentage. A healthy server can therefore show I/O 0%, which looks like missing telemetry even though it means no measured stall. Ordered Masonry assigns cards round-robin, so a later server can visually start above an earlier server. Failure states also compete with host metadata and relative freshness copy in the card header.

The dashboard must distinguish SSH failure, stale telemetry, collector failure, and partial GPU visibility while retaining dense access to the last trustworthy snapshot. New monitoring must not create meaningful server load.

## Chosen architecture

Use the existing ten-second SSH collection cadence and existing system command. Extend that single remote Python command with cheap kernel-file reads:

- /proc/diskstats cumulative counters for read/write throughput.
- /sys/bus/pci/devices vendor/class files for NVIDIA display-controller count.
- Existing /proc/pressure/io PSI and /proc/stat blocked tasks remain the stall signal.

No extra SSH connection, lspci, iostat, daemon, package, or high-frequency poll is introduced. Backend collectors calculate deltas between snapshots and expose compact semantic fields. GPU visibility compares the current NVIDIA list with PCI inventory and the collector's learned or historical high-water mark; a mismatch must persist for two samples before it becomes degraded.

## I/O semantics

Collapsed System preview must never show a bare I/O 0%.

- No PSI stall and negligible traffic: I/O 여유
- Active traffic without pressure: compact throughput, for example R 84 · W 12 MB/s
- Meaningful PSI pressure or blocked tasks: I/O 병목
- Unsupported or unavailable: I/O –

Expanded System shows read MB/s, write MB/s, PSI some/full, blocked tasks, and sample age. PSI is described as stall pressure, not utilization.

## GPU visibility health

Status reasons are orthogonal to telemetry truth.

- gpu_device_missing: SSH and system collection work, but fewer GPUs are visible to NVIDIA tooling than expected.
- Reason copy includes visible and expected count and, when known, missing indices.
- The card retains visible GPU rows and last-known context; it does not mark missing GPUs as available.
- Detection uses two consecutive mismatches to avoid a one-poll transient.
- A historical or learned expected count is used without adding a per-server polling command.

## Ordered Masonry

Manual server order remains authoritative. Placement must satisfy:

- DOM order is never changed.
- For placements p[i], p[i].gridRowStart is not greater than p[i+1].gridRowStart.
- Columns are chosen from the currently shortest columns, with stable left-to-right tie breaking.
- A later card may leave a small gap rather than start above an earlier card.
- Existing FLIP animation continues to animate layout changes.

## Failure-state card

Card headers keep one stable baseline: server name, status mark, host, edit affordance. Relative freshness strings are removed from the baseline.

For stale, degraded, unknown, or offline states:

- A semantic veil covers the card body with a material-aware translucent or opaque treatment.
- Primary copy identifies the condition with a bounded operational vocabulary: SSH 연결 실패, GPU 인식 누락, 수집 지연, 수집 중단, GPU 메트릭 수집 실패, 시스템 메트릭 수집 실패, 메트릭 수집 실패, or 상태 확인 중. Granular collector labels are intentional because the user needs to distinguish transport, GPU, system, and freshness failures.
- Secondary copy provides the actionable reason and a compact last-known age only inside the veil.
- Pointer hover or keyboard focus softens or removes the blur so the last snapshot can be inspected.
- The status message remains readable while detail is revealed.
- Reduced-motion users get an immediate state change.
- Offline and stale cards never claim GPU availability.

## Theme and shortcut UX

The menu group is named Theme / Material.

Presets:
- Clean, renamed from Liquid Glass: crisp, quiet, mostly opaque, lowest decorative depth.
- Claude+: warm paper-like surfaces, softer borders, warm shadow tone.
- AstroVista: cooler technical surfaces, tighter radius, crisper separators and depth.

Preset changes alter structural material variables such as surface opacity, blur, border strength, radius, shadow, and control treatment, not only color tokens.

Shortcut discovery is contextual:
- View: V
- Internal, External, All: 1, 2, 3
- Light or Dark: C

Controls expose accessible labels and a restrained hover or focus tooltip. The View menu also includes one compact shortcut legend.

## Development scenarios

Development-only scenarios include stale telemetry, I/O bottleneck, SSH offline, GPU visibility mismatch, and a mixed failure set. Scenarios transform client-side snapshots only and never write backend or live data.

## Acceptance criteria

1. Healthy zero PSI is presented as no stall, not ambiguous zero I/O.
2. Throughput values derive from cumulative disk counters and elapsed time.
3. GPU mismatch can be detected without an extra polling command.
4. A later ordered server never starts visually above an earlier server in Masonry.
5. Failure header alignment remains stable regardless of reason length.
6. Offline veil is readable and deblurs on hover or focus.
7. Theme names and material behavior match this document.
8. Shortcut help is discoverable without permanent clutter.
9. Poll interval remains ten seconds and no new dependency or remote daemon is added.
10. Frontend contracts, check, build, backend unit tests, and browser QA pass.
