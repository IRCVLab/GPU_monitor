# Design

## Source of truth

- Status: Active.
- Last refreshed: 2026-07-14.
- Primary product surfaces: GPU monitoring dashboard `Full` card view, `Compact` availability-only view, adaptive header, server administration, notes/memos/soft holds, event logs, and diagnostics.
- Repository/branch: `~/workspace/monitoring_v2_dev`, `feature/compact-gpu-dashboard`.
- Active contracts: this root `DESIGN.md` and `docs/superpowers/specs/2026-07-14-dense-apple-gpu-monitor-design.md` are the only active design contracts.
- Supersession: every earlier dashboard/header/compact spec or plan under `docs/superpowers/specs/` and `docs/superpowers/plans/` is historical where it conflicts with this document or the dense Apple GPU monitor spec. Prior terms such as `Default`, persistent right rail, scrollable GPU strip, density setting UI, density cookies, or layout-width preference are superseded. The user-facing card-dashboard name is `Full`, not `Default`.
- Product job: researchers scan independent, non-high-speed-networked GPU servers to find an empty server or exact GPU and identify who is already using occupied GPUs.
- Evidence reviewed:
  - `DESIGN.md`
  - `docs/superpowers/specs/2026-07-14-dense-apple-gpu-monitor-design.md`
  - `docs/superpowers/specs/2026-07-14-adaptive-dashboard-header-design.md`
  - `docs/superpowers/specs/2026-07-14-compact-gpu-dashboard-design.md`
  - `docs/superpowers/specs/2026-07-14-apple-dashboard-refinement-design.md`
  - `docs/superpowers/plans/2026-07-14-adaptive-dashboard-header.md`
  - `docs/superpowers/plans/2026-07-14-compact-gpu-dashboard-implementation.md`
  - `docs/superpowers/plans/2026-07-14-apple-gpu-monitor-dashboard.md`
  - `frontend/package.json`
  - `frontend/src/routes/+page.svelte`
  - `frontend/src/app.css`
  - `frontend/src/lib/styles/monitor-dashboard.css`
  - `frontend/src/lib/styles/monitor-cards.css`
  - `frontend/src/lib/styles/monitor-compact.css`
  - `frontend/src/lib/components/ServerCard.svelte`
  - `frontend/src/lib/components/GpuBar.svelte`
  - `frontend/src/lib/components/CompactDashboard.svelte`
  - `frontend/src/lib/components/CompactServerRow.svelte`
  - `frontend/src/lib/components/CompactServerDetail.svelte`
  - `frontend/src/lib/components/NoteForm.svelte`
  - `frontend/src/lib/stores/order.ts`
  - `frontend/src/lib/stores/dashboardPrefs.ts`
  - `frontend/src/lib/types.ts`
  - `backend/models.py`
  - `backend/routers/notes.py`
  - `backend/note_expiry.py`
  - `backend/collectors/gpu.py`
  - `backend/ws_manager.py`
- Exact theme/token source: `https://tweakcn.com/themes/cmr2flrsp000304ih46yj4y1b?p=marketing` (`Apple Liquid Glass`). The listed values were extracted from the theme payload and are authoritative.
- Secondary design-language reference: Apple Human Interface Guidelines, `https://developer.apple.com/design/human-interface-guidelines/`. This informs restraint and platform feel only; it does not override the extracted theme tokens.
- Reference baseline: `cf70ad0` small-density Full-card screenshot/behavior.

## Brand

- Personality: dense, exact, calm, technical, research-lab pragmatic, and quietly premium.
- Trust signals: manual server order, exact `G#`, visible Linux usernames, clear freshness/status, plain memo preservation, owner/admin-authenticated deletion, and separated destructive/admin actions.
- Avoid: auto-sort, hidden ownership, persistent unused detail rails, horizontal page scroll, scrollable GPU strips, marketing whitespace, excessive glass/glow, gradients invented from the token export, sharing/export, Slack profiles, hard scheduling, and production edits.

## Product goals

- Goals:
  - Preserve `Full` / `Compact` mode switching.
  - Keep manually ordered `currentServers`; never auto-sort.
  - Let researchers find an empty server or exact GPU and identify current users quickly.
  - Keep Full fixed at the old `작게` density and use dense masonry aiming for three columns at 1440px framed width.
  - Make Compact an availability-only full-width matrix/list with exact slot labels, no persistent rail, and no horizontal page scroll.
  - Add phase-1 advisory GPU soft holds as a backward-compatible Note extension only.
- Non-goals:
  - Slack profiles, Slack avatars, Linux-to-Slack mapping, email matching, hard scheduling, hard locks, share/export, new dependencies, collector changes, WebSocket payload changes, production repo edits, pushes, and deployment.
  - Density setting UI, density cookies, layout-width preference, generic bootstrap code, full-screen reveal effects, and standalone gradient/surface-opacity tokens.
- Success signals:
  - Researchers can scan visible servers in manual order and distinguish free, occupied, unknown, and stale states without opening a persistent rail.
  - Exact `G#`, integer memory, and full usernames remain available.
  - Header hide/reveal reclaims layout space without overlapping cards.
  - Plain memos continue to render and behave as before while holds overlay exact GPUs advisory-only.

## Personas and jobs

- Primary personas: researchers choosing compute for training jobs; administrators maintaining server metadata, order, and notes.
- User jobs:
  - Find an empty independent server or exact free GPU.
  - Identify who is already using occupied GPUs.
  - Check whether telemetry is fresh enough to trust.
  - Add a plain memo or advisory hold without changing actual GPU access.
  - Register, edit, reorder, debug, log-inspect, or delete servers through admin flows.
- Key contexts of use: frequent desktop lab checks, narrow/mobile checks, dark-room monitoring, mixed Korean/English labels, and urgent pre-training server selection.

## Information architecture

- Primary navigation:
  - Adaptive header contains status/freshness on the left, network selector, `View`, `Manage`, and far-right sun/moon mode toggle.
  - `View` contains dashboard mode (`Full` / `Compact`) and color theme.
  - `Manage` contains registration, logs, debug, and delete management.
- Core routes/screens:
  - Dashboard route hosts `Full` and `Compact`.
  - Logs and debug remain secondary diagnostic routes.
  - Notes/memos/holds are server-attached overlays, not separate scheduling surfaces.
- Content hierarchy:
  - Header state and network scope.
  - Manual-order server scan.
  - Exact per-GPU state and Linux usernames.
  - Memo/hold annotations.
  - Secondary system/storage/debug/admin details only where they support choosing or maintaining servers.

## Design principles

- Exactness beats summary: preserve `G#`, user lists, and telemetry truth.
- Manual order is admin intent: filtering may hide servers, but no view sorts automatically.
- Density beats marketing: cards, rows, popovers, and overlays are compact and operational.
- Progressive detail: Full cards expose richer server detail; Compact shows availability first and uses temporary detail only when selected.
- Advisory holds are annotations: telemetry remains truth and holds do not enforce exclusivity.
- Tradeoffs: prefer less decorative glass and fewer layout modes over visual novelty; prefer backward-compatible notes changes over a scheduling subsystem.

## Visual language

- Color:
  - Use exact light/dark tokens below.
  - Availability/free: strong semantic green.
  - Utilization: strong restrained emerald.
  - Memory: restrained blue.
  - Occupied: neutral/blue with deterministic initials.
  - Unknown/stale: amber warning treatment.
  - Offline/destructive: semantic red.
  - Do not invent gradient-stop tokens, standalone surface-opacity tokens, or decorative gradients from the reference export.
- Typography:
  - Native system stack only; no remote reference font.
  - Use tabular numerals for metrics.
  - Letter spacing token is `0em`.
- Spacing/layout rhythm:
  - Base spacing token is `0.25rem`.
  - Full targets dense three-column masonry at 1440px framed width.
  - Compact uses a full-width wrapping matrix/list with no page or row horizontal scroll.
- Shape/radius/elevation:
  - Radius token is `1.5rem`.
  - Shadows use supplied color, opacity, blur, offset, and `shadow-spread 0px`.
  - Frosted opacity is allowed only as a component treatment using semantic color-mix; it is not an extracted standalone token.
- Motion:
  - Restrained 140-240ms transitions.
  - Scroll handlers use `requestAnimationFrame` and thresholding.
  - Existing immediate theme mode switch remains; generic 600ms circular reveal is not required.
  - Slower status breathing only where it does not distract.
- Imagery/iconography:
  - Deterministic initials are functional identity marks.
  - Icons support state and navigation; they do not replace text for critical state.

### Light tokens

```text
background #f4f5f7
foreground #0c121a
card #ffffff
card-foreground #0c121a
popover #ffffff
popover-foreground #0c121a
primary #297cef
primary-foreground #ffffff
secondary #e9ebee
secondary-foreground #222933
muted #eceff1
muted-foreground #565e69
accent #d9e6f9
accent-foreground #002c78
destructive #ee343b
destructive-foreground #ffffff
border #dbdee2
input #e2e5e8
ring #297cef
chart-1 #297cef
chart-2 #00a381
chart-3 #864ad2
chart-4 #f3680f
chart-5 #ec2773
sidebar #eceff1
sidebar-foreground #0c121a
sidebar-primary #297cef
sidebar-primary-foreground #ffffff
sidebar-accent #d9e6f9
sidebar-accent-foreground #002c78
sidebar-border #dbdee2
sidebar-ring #297cef
radius 1.5rem
shadow-color #4e5661
shadow-opacity 0.10
shadow-blur 28px
shadow-spread 0px
shadow-offset 0 2px
spacing 0.25rem
letter-spacing 0em
```

### Dark tokens

```text
background #090b0f
foreground #f0f2f4
card #13161b
card-foreground #f0f2f4
popover #1a1d22
popover-foreground #f0f2f4
primary #3a8cff
primary-foreground #040609
secondary #1c2024
secondary-foreground #d9dfe5
muted #181b1f
muted-foreground #8f9aa4
accent #152946
accent-foreground #a5d0ff
destructive #ff515a
destructive-foreground #ffffff
border #26292e
input #26292e
ring #3a8cff
chart-1 #3a8cff
chart-2 #00b793
chart-3 #9b61ea
chart-4 #ff7527
chart-5 #fb3a7f
sidebar #0f1216
sidebar-foreground #f0f2f4
sidebar-primary #3a8cff
sidebar-primary-foreground #040609
sidebar-accent #152946
sidebar-accent-foreground #a5d0ff
sidebar-border #212429
sidebar-ring #3a8cff
radius 1.5rem
shadow-color #000000
shadow-opacity 0.45
shadow-blur 40px
shadow-spread 0px
shadow-offset 0 4px
spacing 0.25rem
letter-spacing 0em
```

## Components

- Existing components to reuse:
  - `ServerCard`, `GpuBar`, `StatusBadge`, `NoteForm`, `ServerForm`, `ServerDeleteModal`, stores, API wrappers, and existing dashboard CSS layers.
- New/changed components:
  - `Full` mode display and label semantics where prior copy says `Default`.
  - Compact server matrix/list rows and viewport-safe temporary detail overlay/popover.
  - Notes rendering for `kind='memo'|'hold'` while preserving existing memo behavior.
  - Header adaptive flow/collapse behavior and control grouping.
- Variants and states:
  - View: `Full`, `Compact`.
  - Server: online, degraded, offline, unknown, stale telemetry.
  - GPU: free, occupied, held advisory overlay, unknown, stale telemetry.
  - Note: memo, active hold, near-expiry hold, expired/omitted hold.
  - Header: expanded in flow, collapsed with translated/faded header, restored on upward scroll, locked/restored for hover/focus/menu.
- Token/component ownership:
  - Tokens live in global CSS/theme layers.
  - Component CSS consumes semantic tokens and may use color-mix frost only for header, popover, and selected surfaces.
  - No new design-system layer or dependency.

## Accessibility

- Target standard: WCAG 2.2 AA where practical.
- Keyboard/focus behavior:
  - Network selector, `View`, `Manage`, sun/moon, Full cards, Compact rows, GPU groups, notes, hold controls, overlays, and dialogs are keyboard reachable.
  - Hover-only username reveal must have focus and touch equivalents.
  - Upward scroll restores the header; focus/hover details must keep controls reachable.
- Contrast/readability:
  - State is not color-only; pair with text, icon, shape, or label.
  - Initials, `+N`, metrics, warnings, and focus rings meet contrast expectations in light and dark modes.
- Screen-reader semantics:
  - GPU cells announce exact `G#`, free/occupied/held/unknown/stale state, integer utilization/memory, and usernames where available.
  - Soft holds announce owner, selected GPUs, created time, and expiry/countdown.
- Reduced motion and sensory considerations:
  - Disable nonessential header/card/overlay movement under `prefers-reduced-motion`.
  - Avoid excessive glow, breathing, and full-screen reveal motion.

## Responsive behavior

- Supported breakpoints/devices:
  - Desktop: Full targets three dense masonry columns at 1440px framed width; Compact is full-width matrix/list.
  - Tablet: Compact wraps GPU groups and keeps header controls reachable.
  - Mobile: viewport-safe overlay/sheet for selected Compact details; no page/row horizontal scroll.
- Layout adaptations:
  - Expanded header participates in normal flow.
  - On intentional down scroll, the reserved header block collapses while the header translates/fades.
  - Desktop indicator dot is allowed only when an outer gutter exists, with 12-16px top offset.
  - On narrow widths, no floating dot is shown.
- Touch/hover differences:
  - Hover/focus detail temporarily reserves its own slim row or restores the header; it never overlays cards.
  - Mobile uses tap/focus overlays instead of hover-only affordances.

## Interaction states

- Loading: preserve final layout rhythm; do not show giant blank hero space.
- Empty: distinguish no registered servers, no servers in selected network scope, and registered servers without telemetry.
- Error: retain last-known data when safe, label stale state, and offer retry.
- Success: refresh state updates quietly; holds/memos show confirmed creation or deletion without changing telemetry truth.
- Disabled: explain unavailable controls with text, not opacity alone.
- Offline/slow network, if applicable:
  - Header transport status is separate from per-server collector status.
  - Stale/unknown telemetry shows warning and never claims availability.
- Header:
  - Expanded header participates in flow.
  - Intentional down scroll collapses the reserved block while header translates/fades.
  - Upward scroll restores the header.
  - Hover/focus detail reserves a slim row or restores the header; it never overlays cards.
- Compact selection:
  - Desktop selection opens only a conditional temporary anchored popover/overlay.
  - No aside, placeholder, reserved second column, or persistent empty region is rendered when unselected.
  - Mobile selection opens a viewport-safe overlay/sheet.
  - Compact never causes page or row horizontal scroll.

## Content voice

- Tone: terse, operational, calm, research-lab practical.
- Terminology:
  - User-facing mode names are `Full` and `Compact`.
  - Use exact `G#` labels.
  - Use `Linux username`, `memo`, `hold`, `expires`, and `stale telemetry` precisely.
  - GPU memory uses integer `21/24 GB`; utilization uses integer `78%`.
- Microcopy rules:
  - Do not imply holds reserve GPUs exclusively.
  - Do not use `Default` for the Full view.
  - Do not call expired holds stale; stale refers to telemetry freshness.
  - Avoid card-level marketing copy and visible free-count slogans.

## Implementation constraints

- Framework/styling system: SvelteKit 5, TypeScript, Tailwind, and existing repo CSS layers.
- Design-token constraints:
  - Use only the supplied light/dark tokens plus `shadow-spread 0px`, `spacing 0.25rem`, and `letter-spacing 0em`.
  - Do not extract standalone `surface-opacity` or gradient-stop tokens.
  - Frosted opacity can use semantic color-mix as component treatment only.
- Soft-hold YAGNI contract:
  - Backward-compatible Note extension only: `kind 'memo'|'hold'`, `gpu_indices number[]`, existing `username`, `content`, `created_at`, and `expires_at`.
  - Storage may add `notes.kind` default `'memo'` and `notes.gpu_indices` nullable JSON text; API serializes `gpu_indices` as `number[]`.
  - Create request defaults to `kind='memo'` and `gpu_indices=[]`.
  - Memo must have no GPU indices.
  - Hold requires at least one unique non-negative integer GPU index; normalize indices ascending.
  - Cancellation is deletion through the existing owner/admin-authenticated `DELETE` path.
  - Do not add `cancelled_at`, status fields, scheduler, overlap rejection, or hard exclusivity.
  - Expired holds are cleaned/omitted by current expiry behavior; near-expiry shows a countdown.
  - Stale means telemetry freshness, not a stale hold record.
  - Holds are advisory overlays; current telemetry remains truth for actual occupancy.
  - Notes API/storage may change; collector and WebSocket payload contracts may not change.
  - Plain memo behavior and rendering remain.
- Header no-overlap contract:
  - Expanded header participates in flow.
  - On intentional down scroll, the reserved block collapses while header translates/fades.
  - Desktop indicator dot is allowed only with an outer gutter and 12-16px top offset.
  - No floating dot on narrow widths.
  - Hover/focus detail reserves a slim row or restores the header; it never overlays cards.
  - Upward scroll restores the header.
- Performance constraints:
  - No new dependencies for masonry, Compact layout, notes/holds, or header motion.
  - Use `requestAnimationFrame` for scroll calculations and avoid layout thrash.
- Compatibility constraints:
  - No CSS zoom, no production repo edit, no push/deployment, and no collector/ws payload changes.
- Test/screenshot expectations:
  - `git diff --check` for docs-only changes.
  - Future implementation must run frontend static checks/build and visual QA in dark/light 1440px framed width and mobile.
  - Soft-hold acceptance tests: exact `G#` hold create; plain memo regression; owner delete success; admin delete success; non-owner delete returns 403; expiry omission; stale telemetry warning; no collector/WebSocket contract diff.

## Open questions

- [x] Active contracts resolved: only root `DESIGN.md` and `docs/superpowers/specs/2026-07-14-dense-apple-gpu-monitor-design.md` are active; prior specs/plans are historical where conflicting.
- [x] User-facing naming resolved: `Full`, not `Default`.
- [x] Compact detail resolved: temporary anchored desktop popover/overlay only; mobile viewport-safe overlay/sheet; no persistent rail or reserved second column.
- [x] Soft-hold scope resolved: backward-compatible advisory Note extension only, no scheduler/status/cancelled_at/hard lock.
- [x] Header overlap resolved: in-flow expanded header, collapsing reserved block, no card overlay by hover/focus details.
- [x] Token export resolved: no standalone surface-opacity or gradient-stop tokens; frost is component treatment only.
- [x] Self-review resolved: no placeholder markers or unresolved ambiguity remain in the active design contract.

## Historical docs

- Historical where conflicting:
  - `docs/superpowers/specs/2026-07-14-adaptive-dashboard-header-design.md`
  - `docs/superpowers/specs/2026-07-14-compact-gpu-dashboard-design.md`
  - `docs/superpowers/specs/2026-07-14-apple-dashboard-refinement-design.md`
  - `docs/superpowers/plans/2026-07-14-adaptive-dashboard-header.md`
  - `docs/superpowers/plans/2026-07-14-compact-gpu-dashboard-implementation.md`
  - `docs/superpowers/plans/2026-07-14-apple-gpu-monitor-dashboard.md`
- Keep useful evidence from those files only when it does not conflict with this document or the dense Apple GPU monitor spec.
