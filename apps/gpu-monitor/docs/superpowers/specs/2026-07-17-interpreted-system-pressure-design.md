# Interpreted System Pressure Design

## Status

- Status: Approved for planning.
- Date: 2026-07-17.
- Scope: DEV `Full` server-card System summary and expanded System diagnostics only.
- Product decision: default UI communicates interpreted contention; raw Linux metrics remain secondary diagnostics.
- Non-scope: Compact GPU availability layout, collection cadence, backend polling protocol, LIVE deployment, and threshold customization UI.

## Problem

The collapsed System row currently renders `부하 {load_avg_1} · CPU {cpu_count}`. That notation resembles a fraction or capacity maximum even though Linux load average is not normalized and can exceed the logical CPU count. The hover explanation adds information but does not repair the first-read failure.

The dashboard user needs to answer two different questions:

1. How much CPU, RAM, and storage is currently used?
2. Are tasks actually waiting because CPU or I/O is congested?

Raw load average is useful for expert diagnosis but is not an appropriate default answer to either question.

## Metric semantics

- CPU utilization is busy CPU time as a percentage.
- Linux load average is the average count of runnable or uninterruptible tasks over 1, 5, and 15 minutes. It is not a percentage and is not normalized by CPU count.
- PSI is Pressure Stall Information.
- PSI `some avg10` is the rolling percentage of wall-clock time in which at least one task was stalled by the resource.
- I/O PSI `full avg10` is the rolling percentage of wall-clock time in which all non-idle tasks were simultaneously stalled on I/O.
- PSI percentages do not sum per-task wait time. Ten tasks waiting during the same one second still represent one second of wall-clock pressure.
- The kernel also exposes cumulative `total` stall time, but this UI does not need it for the current at-a-glance job.

## Design decision

### Collapsed System summary

Always show direct capacity facts in one line:

```text
CPU 7% · RAM 31% · 저장 87%
```

Do not show raw load average, logical CPU count, PSI terminology, a healthy verdict, or an explanatory tooltip in the collapsed state.

Append one interpreted exception only when telemetry indicates meaningful contention:

```text
CPU 95% · RAM 19% · 저장 62% · CPU 혼잡
CPU 82% · RAM 44% · 저장 68% · CPU 병목
CPU 42% · RAM 38% · 저장 71% · I/O 혼잡
CPU 42% · RAM 38% · 저장 71% · I/O 병목
```

If both CPU and I/O have the same highest severity, use `CPU·I/O 혼잡` or `CPU·I/O 병목`. If severities differ, show only the more severe cause. When telemetry is historical, render the exact collapsed copy `마지막 수집값 · CPU – · RAM – · 저장 –` and never append a contention label.

The exception cue is text-first and uses restrained semantic color. It is not a pill, gauge, decorative dot, or always-visible status label.

### Interpreted thresholds

Reuse the existing classifier thresholds and internal levels, but rename the user-facing `pressure` copy from `압박` to `혼잡`:

- PSI `avg10 < 5%`: no collapsed contention label.
- PSI `avg10 >= 5%` and `< 20%`: `혼잡`.
- PSI `avg10 >= 20%`: `병목`.
- A positive blocked-task count promotes I/O to at least `혼잡`.
- CPU PSI `some avg10` determines CPU contention.
- I/O PSI takes the maximum severity of `some avg10` and `full avg10`.
- The expanded interpreted I/O percentage displays `io_pressure_some avg10`. `io_pressure_full avg10` may escalate severity and remains visible only as `I/O 전체 지연` in diagnostics.

CPU utilization, RAM utilization, storage utilization, normalized load, and queue length remain supporting facts. They do not independently produce a CPU/I/O contention label because high utilization alone does not prove that work is stalled.

### Expanded System diagnostics

Keep the dense resource overview first:

```text
CPU 95%   RAM 19%   저장 62%
```

Replace the current opaque `병목 단서` presentation with an interpreted pressure section:

```text
작업 지연
CPU  혼잡  9.7% · 최근 10초
I/O  원활  0.0% · 최근 10초
```

When a pressure value is unavailable, show `확인 불가` rather than inferring a healthy state.

Retain expert diagnostics below, visually quieter:

```text
진단
Load 1·5·15분  20.7 · 19.4 · 18.1
실행 중  21
논리 CPU  20
I/O 전체 지연  0.0%
차단 작업  0
```

The labels must make the values self-describing. Do not rely on native `title` tooltips for essential interpretation. Accessible names may include the full definition, but ordinary comprehension must come from visible labels.

## Information hierarchy

1. GPU availability and ownership remain the card's primary content.
2. Collapsed System shows compact resource usage.
3. Only actual contention receives an exception cue.
4. Expanded System shows interpreted waiting before raw diagnostic values.
5. Raw load and PSI terminology remain available for experts without dominating the card.

## Visual behavior

- Preserve the current dense one-line collapsed rhythm.
- Use existing warning and destructive semantic colors only for `혼잡` and `병목`.
- Do not add a new legend, meter, gauge, tooltip dependency, or color family.
- Preserve the mounted symmetric disclosure animation and reduced-motion behavior.
- Keep all figures tabular and aligned.
- No layout reordering and no card-order changes.

## Accessibility

- The System disclosure retains `aria-expanded` and `aria-controls`.
- Visible text communicates the interpretation without hover.
- Screen-reader output identifies resource, severity, percentage, and time window.
- Unknown and historical states are not communicated by color alone.
- Reduced-motion behavior remains unchanged.

## Alternatives considered

### Normalized load percentage

`load_avg_1 / cpu_count * 100` creates a familiar percentage but combines CPU-runnable and uninterruptible-I/O tasks, can exceed 100%, and still needs explanation. It remains unsuitable as the primary collapsed cue.

### Always-visible server verdict

`서버 여유 / 서버 압박 / 서버 병목` answers the question directly but duplicates the server connectivity status and adds noise to every healthy card. Exception-only cause labels are quieter and more precise.

### Raw load average with tooltip

This preserves expert convention but repeats the current usability failure. Essential meaning cannot depend on hover.

## Implementation and test impact

- Update `frontend/src/lib/utils/resourcePressure.ts` and its tests so the internal `pressure` level keeps the same thresholds while visible copy becomes `혼잡`.
- Update `frontend/src/lib/components/ServerCard.note-contract.test.ts` to remove the existing collapsed load/tooltip contract and assert interpreted exception cues, visible expanded delay labels, and historical behavior.
- Add pure cause-selection coverage for unequal severity, equal severity, blocked-task promotion, unavailable data, and historical data.
- Keep existing page-view contracts for disclosure motion, server ordering, and reduced motion.

## Acceptance criteria

- Collapsed System contains CPU, RAM, and storage percentages.
- Collapsed System contains no raw `부하`, `Load avg`, logical CPU count, or load tooltip.
- Healthy PSI values add no contention label.
- CPU PSI pressure/bottleneck renders `CPU 혼잡`/`CPU 병목`.
- I/O PSI pressure/bottleneck renders `I/O 혼잡`/`I/O 병목`.
- CPU 혼잡 + I/O 병목 renders only `I/O 병목`.
- CPU 병목 + I/O 혼잡 renders only `CPU 병목`.
- Equal CPU/I/O severity renders `CPU·I/O 혼잡` or `CPU·I/O 병목`.
- Positive `io_blocked_tasks` with otherwise-low PSI renders at least `I/O 혼잡`.
- Expanded System visibly names CPU `some avg10` and I/O `some avg10` as recent-10-second delay percentages.
- Expanded diagnostics retain raw 1/5/15-minute load, runnable tasks, logical CPU count, I/O full, and blocked tasks.
- Unknown telemetry uses `확인 불가`; historical collapsed telemetry renders exactly `마지막 수집값 · CPU – · RAM – · 저장 –` with no contention label.
- Existing disclosure motion, server order, GPU content, and LIVE service remain unchanged.
- Contract tests, `svelte-check`, production build, and Playwright desktop/mobile checks pass.
