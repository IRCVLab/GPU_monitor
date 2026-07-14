# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-14
- Primary product surfaces: Compact GPU dashboard as the active new surface, preserved default server-card dashboard, server administration, notes, event logs, development diagnostics, Slack channel notifications, `/gpu` Slack command.
- Product purpose: help researchers choose which independent GPU server to enter for training. Servers are not high-speed-networked, so the decisive questions are which exact server has capacity, which exact GPU slots are occupied, and who is already using each GPU.
- Evidence reviewed:
  - `DESIGN.md`
  - `feature/slack.md`
  - `frontend/package.json` (SvelteKit 5, Tailwind, Vite)
  - `frontend/src/routes/+page.svelte`
  - `frontend/src/lib/components/ServerCard.svelte`
  - `frontend/src/lib/components/GpuBar.svelte`
  - `frontend/src/lib/components/StatusBadge.svelte`
  - `frontend/src/lib/stores/order.ts`
  - `frontend/src/lib/stores/dashboardPrefs.ts`
  - `frontend/src/lib/stores/servers.ts`
  - `frontend/src/lib/types.ts`
  - `frontend/src/lib/api.ts`
  - `backend/config.py`
  - `backend/models.py`
  - `backend/routers/metrics.py`
  - `backend/routers/servers.py`
  - `backend/routers/slack.py`
  - `backend/slack_client.py`
  - `backend/slack_gpu.py`
  - `backend/slack_socket.py`
  - `backend/collectors/gpu.py`
  - `backend/collectors/server_collector.py`
  - Slack official docs: `https://docs.slack.dev/reference/methods/users.list/`
  - Slack official docs: `https://docs.slack.dev/reference/scopes/users.read.email/`
- Supersedes: earlier statements that Compact is deferred. Compact is now the active new surface, while the default card dashboard is preserved as a separate existing view.

## Brand
- Personality: dense, exact, calm, technical, research-lab pragmatic, quietly premium.
- Trust signals: manual server order is preserved, GPU slot numbers remain exact, users are tied to the GPU they occupy, freshness and network scope stay visible, Slack identity enrichment never blocks telemetry, and destructive/admin actions remain separated.
- Avoid: giant hero areas, operations-command-center drama, decorative gradients, visible card-level free-count copy, hidden ownership, auto-sorting that overrides admin intent, sharing/export features, and Slack email matching unless explicitly chosen later.

## Product goals
- Goals:
  - Add Compact as a separate view for dense server-by-server GPU selection; do not replace or degrade the existing default card dashboard.
  - Preserve admin-controlled manual server order in every dashboard view; no automatic sorting by availability, status, or name.
  - Represent each server as one dense row in Compact.
  - Retain exact GPU slot labels (`G0`, `G1`, `G2`, ...); never renumber or compact slots visually.
  - Show compact user previews in the main list tied to each GPU slot.
  - Use Slack profile photos first with deterministic initials fallback.
  - Let users inspect a selected row in a desktop right detail panel or mobile bottom sheet.
  - Keep the existing header, network scope controls, Manage/actions, theme controls, live refresh, polling fallback, notes, logs, debug, CRUD, and default dashboard behavior intact.
- Non-goals:
  - Replacing the default server-card dashboard.
  - Changing backend collector behavior, SSH credentials, authentication, notes, logs, debug, Slack channel alert semantics, or `/gpu` command semantics.
  - Adding sharing, export, public links, or card-level “free count” marketing copy.
  - Introducing email-based Slack matching by default.
  - Editing or deploying the production repo at `~/workspace/monitoring_v2` as part of this design branch.
- Success signals:
  - Researchers can scan all servers in Compact without opening cards and can still identify exact GPU ownership.
  - Manual order is visibly stable across Default, Compact, Internal, External, and All scopes.
  - A selected Compact row has subtle emphasis, not a modal takeover.
  - Detail panel shows all usernames without truncation plus per-GPU utilization and integer memory.
  - Slack profile failures degrade to initials without slowing or blocking dashboard telemetry.

## Personas and jobs
- Primary personas: researchers choosing compute for training jobs; administrators maintaining server metadata, order, and optional Linux-to-Slack mappings.
- User jobs:
  - Choose an independent server based on immediate GPU availability and current users.
  - Compare network scope, server identity, status, GPU model, exact GPU slots, utilization, memory, and user occupancy quickly.
  - Hover or focus a compact avatar preview to see full names before selecting a row.
  - Open a row detail panel when the compact preview is not enough.
  - Register, edit, reorder, or delete a server through existing administration flows.
  - Confirm explicit Linux username to Slack user mappings when Slack suggestions are available.
- Key contexts of use: frequent desktop lab checks, dark-room monitoring, phone-width checks, mixed Korean labels and technical metrics, and quick server selection before starting or moving training work.

## Information architecture
- Primary navigation:
  - Keep the current header: identity/freshness on the left, Internal/External/All scope controls, View/Manage/theme controls.
  - Add Compact as a view option beside the preserved default dashboard view.
  - Network scope remains global and applies to Compact and Default.
  - Manage remains the entry point for server administration and future mapping confirmation UI.
- Core routes/screens:
  - Dashboard route hosts two views: Default cards and Compact rows.
  - Compact main list is the active new surface.
  - Compact desktop detail appears in a right-side panel; mobile detail appears as a bottom sheet.
  - Event logs and debug remain secondary diagnostic routes.
  - Slack channel notifications and `/gpu` stay backend/Slack surfaces, not dashboard replacement flows.
- Content hierarchy for Compact:
  1. Header controls, network scope, and freshness.
  2. Dense server rows in manual order.
  3. Per-server identity/status/network when relevant.
  4. Exact GPU slots with avatar previews tied to each slot.
  5. Selected-row detail: all usernames, utilization, integer memory, slot state, GPU model.
  6. Secondary system/storage/notes/admin affordances only where they support server choice.

## Design principles
- Compact is a separate surface: optimize density without breaking the default card dashboard.
- Order is admin intent: never auto-sort Compact rows by availability or status.
- One server, one row: each server receives a single dense row regardless of GPU count.
- Slot identity is sacred: preserve exact `G#` labels from telemetry and backend contracts.
- Users are attached to GPUs, not servers: avatar groups sit inside the relevant GPU slot cell.
- Preview first, detail second: the row gives enough to scan; the side panel/bottom sheet gives complete names and metrics.
- Slack enriches identity only: telemetry, dashboard rendering, and availability decisions do not wait for Slack.
- Default remains preserved: card view keeps its refined server-card role and existing data/controls.
- Calm density: use alignment, rhythm, and restrained emphasis instead of hero copy or noisy badges.

## Compact dashboard surface
- Role: active new high-density availability surface for users who need an at-a-glance lab-wide scan.
- Row model:
  - One row per server, rendered in the same manual order used by the default dashboard.
  - Row left: server name, status dot/text, host/port, network label only when scope is All, freshness.
  - Row center: fixed sequence of GPU slot cells labeled `G0`, `G1`, `G2`, etc. using backend `GpuInfo.index` exactly.
  - Row right: compact status/action area for detail affordance; no visible card-level free-count copy.
  - Selected row receives subtle background/border emphasis and keeps layout position.
- GPU slot cell:
  - Shows slot label, quiet utilization/memory micro-bars or numbers only as density permits, and avatar preview.
  - No users: show an empty/available state with calm green or neutral availability mark.
  - One user: one small avatar.
  - Two users: two overlapping avatars.
  - Three or more users: two overlapping avatars plus `+N`, where `N` is the number of hidden additional users.
  - Hover and keyboard focus expose full names for that GPU.
- Detail panel:
  - Desktop: right panel anchored to the dashboard, not a modal, with selected server summary and all GPU rows.
  - Mobile: bottom sheet with the same content and an explicit close control.
  - Shows all usernames without truncation.
  - Shows per-GPU utilization as integer percent and memory as integer `used/total GB`.
  - Shows exact slot labels, GPU model/name, user avatar/initials, and fallback reason when Slack profile is unavailable if useful for admins.
- Existing controls/data preserved:
  - Current header, network filters, View menu, Manage menu, theme mode, color theme, layout width preference, WebSocket/polling refresh, initial load/retry/empty states, server CRUD, delete modal, notes, logs, debug, and manual reorder semantics remain available.
  - Compact must read the same `ServerState`/`GpuInfo` data contract unless a future plan defines a narrow enrichment endpoint for Slack profiles.

## Default dashboard preservation
- The default server-card dashboard remains a first-class view.
- Keep existing card semantics: server cards, per-GPU `GpuBar` rows, system/storage/notes disclosure, edit access, network label in All scope, and current live data behavior.
- Default may continue to refine card visual quality, but Compact acceptance must not require Default replacement.
- Manual reorder behavior must have one source of truth and must not diverge between Default and Compact.

## Slack identity architecture
- Existing Slack backend token policy remains: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, and `SLACK_LOG_CHANNEL` stay backend-only.
- Slack profile enrichment uses `users.list` with `users:read` for workspace members and profile image fields.
- Use Slack `image_*` profile fields for avatars when available; prefer a small dashboard-appropriate image size and retain larger URLs only if needed for high-density screens.
- Persist explicit mappings from `linux_username` to `slack_user_id`.
- Exact-name auto-suggestions may be generated from Slack names/display names but require admin confirmation before becoming mappings.
- Avoid email matching by default. If email matching is later chosen, request `users:read.email` only for that explicit feature because Slack requires it for email fields.
- Cache Slack profiles periodically on the backend; include deactivated/deleted handling and stale-cache timestamps.
- If Slack is unavailable, the token lacks scope, a user is deactivated, a profile image is missing, or no mapping exists, the dashboard falls back to stable initials and full Linux username text.
- Dashboard telemetry must not block on Slack profile fetches, cache refreshes, or mapping suggestion generation.

## Visual language
- Color:
  - Continue the restrained Apple Liquid Glass-inspired token direction already documented for light/dark surfaces.
  - Availability/healthy: semantic green. Delayed/degraded: amber. Offline/destructive: red. Occupied/full: muted neutral. Selected row: subtle primary-tinted border or surface wash.
  - Do not use purple-first gradients or glowing availability effects.
- Typography:
  - Preserve readable Korean/English UI labels and tabular numerals for telemetry.
  - Compact rows rely on numeric alignment and weight contrast; do not increase density by shrinking below readable sizes.
- Spacing/layout rhythm:
  - Compact uses a tight 4px-based rhythm with enough row height for avatar groups and focus rings.
  - No giant hero. Header height and controls should remain close to the current dashboard.
- Shape/radius/elevation:
  - Compact row shell uses shallow radius and low-contrast borders; detail panel uses a clearer surface boundary.
  - Avoid nested glass panels inside every GPU slot.
- Motion:
  - 140-220ms for row hover, selection, panel entry, and tooltip/focus reveal.
  - Mobile sheet uses a short slide/fade that respects reduced motion.
  - No animated reordering or availability drama that implies auto-sort.
- Imagery/iconography:
  - Slack avatars are functional identity marks, not decorative imagery.
  - Initials fallback must remain legible at small sizes and deterministic per user.

## Components
- Existing components to reuse:
  - Header controls and stores in `+page.svelte`.
  - `ServerCard`, `GpuBar`, `StatusBadge`, `ServerForm`, `ServerDeleteModal`, logs, debug route, theme store, order store, server stores, API wrappers.
- New/changed components anticipated by the design contract:
  - Compact view selector state alongside Default.
  - Compact server row shell.
  - GPU slot strip and avatar group primitive.
  - Desktop compact detail panel.
  - Mobile compact bottom sheet.
  - Slack profile/initials presentation primitive.
  - Admin-confirmed mapping surface or workflow entry point.
- Variants and states:
  - View: Default cards, Compact rows.
  - Server: online, degraded, offline, unknown/stale.
  - GPU: available, one user, two users, three-plus users, high utilization, high memory, unknown metrics.
  - Profile: mapped photo, mapped initials fallback, unmapped Linux username, deactivated Slack user, Slack unavailable/stale cache.
  - Detail: none selected, selected, stale data, mobile sheet open/closed.
- Token/component ownership:
  - Global surfaces, typography, radius, shadow, semantic color, and motion tokens live in `app.css`.
  - Component CSS consumes existing tokens; route-specific CSS must not create a competing theme system.

## Accessibility
- Target standard: WCAG 2.2 AA where practical.
- Keyboard/focus behavior:
  - View selector, scope controls, Manage, theme, compact rows, GPU avatar groups, detail close, and default card actions are keyboard reachable.
  - Hover-only full-name reveals must also appear on focus.
  - Selected row state is visible and announced.
  - Mobile sheet traps focus only while open and returns focus to the selected row on close.
- Contrast/readability:
  - Row text, avatar initials, `+N`, status, utilization, and memory must meet contrast expectations in light and dark modes.
  - Availability/status is communicated by text/icon plus color, never color alone.
- Screen-reader semantics:
  - Compact list announces server name, status, network when relevant, freshness, and GPU count.
  - Each GPU slot announces exact slot label, state, utilization, integer memory, and all users.
  - Detail panel/bottom sheet has an accessible name tied to the selected server.
- Reduced motion and sensory considerations:
  - Respect `prefers-reduced-motion` for row transitions, panel animation, bottom-sheet movement, avatar hover effects, and loading shimmer.

## Responsive behavior
- Supported breakpoints/devices:
  - Desktop >=1200px: compact list plus right detail panel.
  - Tablet 768-1199px: compact list with collapsible or overlay detail panel if needed by width.
  - Mobile <768px: compact list plus bottom sheet.
- Layout adaptations:
  - Desktop row should keep server identity and GPU slots visible without horizontal page scroll where practical.
  - If GPU count exceeds available width, the GPU strip may scroll horizontally inside the row while preserving slot order and labels.
  - Mobile rows prioritize server name/status and a horizontally scrollable GPU strip; detail moves to bottom sheet.
- Touch/hover differences:
  - Hover full-name reveals have focus and tap equivalents.
  - Drag reorder remains Default behavior unless a future plan defines touch-safe Compact reorder controls.

## Interaction states
- Loading: compact skeleton rows preserve final row geometry and slot rhythm.
- Empty: distinguish no registered servers, no servers in scope, and no telemetry for registered servers.
- Error: retain last-known data when available, label stale state, and show retry without clearing manual order.
- Success: refresh timestamp updates quietly.
- Disabled: controls explain why they are unavailable; opacity is not the only cue.
- Offline/slow network:
  - Header identifies polling fallback, delayed refresh, or failed refresh.
  - Server collector status remains distinct from dashboard transport state.
- Selection:
  - Selecting a row updates subtle emphasis and opens/updates detail.
  - Selecting another row replaces panel content without reordering the list.
  - Escape closes the panel/sheet where appropriate.
- Slack failures:
  - Missing mapping, missing profile image, deactivated Slack member, missing scope, rate limiting, and Slack outage all render stable initials and usernames.
  - Slack cache status may be shown only in admin/detail contexts, not as noisy row-level warnings.

## Content voice
- Tone: terse, research-lab practical, calm.
- Terminology:
  - Keep existing Korean labels for scope and status: `내부망`, `외부망`, `전체`, `사용 가능`, `사용 중`, `공유`, `정상`, `지연`, `오프라인`, `확인 중`, `마지막 업데이트`.
  - GPU slot labels are exact `G#` labels.
  - GPU memory uses integer `21/24 GB`; utilization uses integer `78%`.
  - Use `Slack profile` and `Linux username` in admin-facing copy when disambiguation is needed.
- Microcopy rules:
  - Avoid visible card-level free-count copy in Compact.
  - Use direct stale/fallback reasons in detail/admin contexts.
  - Do not imply email matching is active unless that feature is explicitly selected later.

## Behavior preservation
- Preserve:
  - SvelteKit app behavior, existing `ServerState`/`GpuInfo` telemetry contracts, WebSocket/polling behavior, collector behavior, and auth behavior.
  - Internal/External/All scope semantics.
  - Server CRUD, delete confirmation, notes behavior, logs, debug route, filters/search where present, layout/theme preferences, and manual order behavior.
  - Existing Slack channel notification and `/gpu` command architecture.
  - Existing production isolation: do not edit `~/workspace/monitoring_v2` for this branch.
- Compact must not:
  - Auto-sort rows.
  - Renumber GPU slots.
  - Hide all usernames behind truncated row text.
  - Depend on Slack for telemetry rendering.
  - Replace Default.

## Implementation constraints
- Framework/styling system: SvelteKit 5, Tailwind, existing global `app.css`/dashboard CSS tokens.
- Design-token constraints:
  - Extend current tokens for compact row, selected row, avatar fallback, and detail panel states.
  - Keep selectable color themes independent from light/dark mode.
- Data/API constraints:
  - Existing `ServerState` includes `server_id`, `server_name`, `host`, `port`, `network`, `status`, `status_reason`, `last_seen`, `gpus`, `system`, `storage`, and `display_order`.
  - Existing `GpuInfo` includes `index`, `name`, `utilization`, `memory_used`, `memory_total`, `temperature`, `power_draw`, and `users`.
  - Slack profile/mapping data should be additive and optional; absence must not change telemetry shape or block rendering.
- Performance constraints:
  - No new frontend dependency for density, avatar grouping, or panel motion unless explicitly approved later.
  - Slack profile cache refresh must be periodic/background and separate from collector loops.
  - Compact list must remain responsive when multiple servers have many GPUs/users.
- Compatibility constraints:
  - Do not use CSS zoom.
  - Do not push or deploy as part of design documentation.
- Test/screenshot expectations for future implementation:
  - Svelte diagnostics and production build.
  - Desktop dark/light at 1440x1000.
  - Mobile dark/light at 390x844.
  - Compact row density, manual order, exact `G#`, avatar grouping, hover/focus names, selected row, right panel, bottom sheet, Slack fallback states, and default-view preservation.

## Open questions
- [x] Compact scope: separate active new surface, not replacement.
- [x] Ordering: preserve admin manual server order; no auto-sort.
- [x] Row model: one dense row per server.
- [x] GPU labels: retain exact `G0`/`G1`/`G#` telemetry numbers.
- [x] User previews: per-GPU compact avatars in main list.
- [x] Avatar rules: Slack photo first, initials fallback; one/two/three-plus grouping as specified.
- [x] Detail: desktop right panel, mobile bottom sheet; all names untruncated; integer utilization/memory.
- [x] Slack architecture: backend-only token, `users:read` for `users.list`, no default email matching, admin-confirmed mappings, non-blocking cache/fallback behavior.
