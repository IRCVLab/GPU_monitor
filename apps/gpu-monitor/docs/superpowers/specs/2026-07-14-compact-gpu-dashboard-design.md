# Compact GPU Dashboard Design Spec

- Date: 2026-07-14
- Status: Approved design direction; awaiting user review before implementation planning.
- Repository/branch: `~/workspace/monitoring_v2_dev`, `feature/compact-gpu-dashboard`
- Scope: Design/documentation only.

## 1. Product goal

Compact is a separate dashboard view for dense GPU selection. It lets researchers scan each independent server in one row, see exact GPU slots, identify who is using each GPU, and open a detail panel only when the compact row is not enough.

The existing default card dashboard remains preserved. Compact does not replace server cards, notes, logs, debug, CRUD, live refresh, WebSocket/polling, network scope controls, theme controls, or manual order behavior.

### Success criteria

- One server appears as one dense row.
- Rows follow admin manual server order; no auto-sort by availability/status/name.
- GPU labels use exact backend slot numbers: `G0`, `G1`, `G2`, and so on.
- Main list shows compact user previews tied to each GPU.
- Slack profile photos render first; stable initials render when Slack data is missing, stale, unavailable, or unmapped.
- Hover and keyboard focus reveal full names for each GPU preview.
- Desktop shows selected server details in a right panel; mobile uses a bottom sheet.
- Detail view shows all usernames without truncation plus per-GPU utilization and integer memory.
- No giant hero, sharing, or visible card-level free-count copy.

## 2. Existing evidence and constraints

### Frontend

- Framework: SvelteKit 5 with Tailwind/Vite (`frontend/package.json`).
- Current dashboard route: `frontend/src/routes/+page.svelte`.
- Current data components: `ServerCard.svelte`, `GpuBar.svelte`, `StatusBadge.svelte`.
- Existing header includes live/freshness status, network scope (`internal`, `external`, `all`), View controls, Manage menu, and theme controls.
- Existing order behavior uses `serverOrder` and `saveOrder` in `frontend/src/lib/stores/order.ts`, with visible servers merged back into global manual order.
- Existing telemetry types:
  - `ServerState`: server identity, host/port, network, status, status reason, last seen, GPUs, system, storage, display order.
  - `GpuInfo`: index, GPU name, utilization, memory used/total, temperature, power draw, users.
- Existing `GpuBar` already formats integer utilization and rounded integer memory GB, exposes an aria label, and renders full usernames in default card rows.

### Backend and Slack

- `backend/config.py` stores Slack configuration as backend settings: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, and `SLACK_LOG_CHANNEL`.
- `backend/slack_client.py` posts channel notifications with `chat.postMessage` and uses cooldown logs.
- `backend/slack_gpu.py`, `backend/routers/slack.py`, and `backend/slack_socket.py` preserve `/gpu` and `/status` command behavior through Socket Mode and a future HTTP slash endpoint path.
- `feature/slack.md` defines channel notifications, spam prevention, fixed message structure, Socket Mode takeover, and Slack-off fallback behavior.
- `backend/models.py` currently has servers, notes, GPU metric history, Slack alert logs, and event logs; explicit Linux-to-Slack mapping storage is a new architecture boundary, not an existing table.

### Official Slack references

- `users.list`: https://docs.slack.dev/reference/methods/users.list/
  - Requires `users:read` for bot/user token access.
  - Returns workspace members, including invited and deleted/deactivated users.
  - User profile data includes `image_*` fields when available.
  - Uses cursor pagination; cache refresh should page through results rather than assuming one response is complete.
- `users:read.email`: https://docs.slack.dev/reference/scopes/users.read.email/
  - Required for email fields in Web API user profiles.
  - Must be requested with `users:read` when email access is needed.
  - Not required for the default compact dashboard because default matching does not use email.

## 3. Information architecture

### Dashboard view structure

- Default view: existing server-card dashboard, preserved.
- Compact view: active new surface.
- Header: current header remains. Network scope controls remain global and affect both Default and Compact.
- View control: offers Default and Compact without changing scope semantics.
- Manage: continues to own server registration, logs, debug, delete, and future mapping confirmation entry points.

### Compact hierarchy

1. Header and freshness/network controls.
2. Compact server row list in manual order.
3. Exact GPU slot cells inside each row.
4. Per-GPU avatar previews.
5. Selected row detail panel/sheet.
6. Admin/mapping/status details only when needed.

## 4. Exact desktop layout

Desktop target: >=1200px.

- Page shell keeps the existing header height and controls.
- Main area is split into a compact list region and a right detail panel.
- If no row is selected, the detail panel may show a quiet prompt or remain collapsed, but it must not insert a hero.
- Row height is dense but must fit focus rings and 20-24px avatars.
- GPU slot strip preserves slot order. If a server has more slots than fit, the row-level slot strip may horizontally scroll; the page should not require horizontal scroll.
- Selected row uses subtle emphasis: one primary-tinted border, small surface wash, or low-opacity inset. It must not jump, resize, or reorder.

### Desktop row anatomy

- Left identity block:
  - Server name.
  - Status dot/text.
  - Host/port.
  - Network label only in All scope.
  - Freshness/last seen.
- Middle GPU strip:
  - Slot cells ordered by backend `gpu.index`.
  - Each cell starts with exact label (`G0`, `G1`, ...).
  - Occupancy preview sits inside the slot cell.
  - Optional micro telemetry remains secondary to user preview.
- Right affordance:
  - Detail affordance or chevron.
  - No visible free-count copy.

### Desktop detail panel anatomy

- Panel header:
  - Server name, status, host/port, network if relevant, last seen.
  - Close control if panel is dismissible.
- GPU detail list:
  - One detail row per GPU slot.
  - Exact `G#`, GPU model/name, utilization integer percent, memory integer `used/total GB`.
  - All usernames, no truncation.
  - Slack avatar/photo or initials next to each user.
- Secondary details:
  - Status reason when not online.
  - Slack profile fallback/cache state only if helpful for understanding identity gaps.
  - Notes/system/storage may remain in Default unless a future plan includes a compact detail subsection.

## 5. Exact mobile layout

Mobile target: <768px.

- Header remains current mobile structure: freshness first, scope controls, Manage/theme access.
- Compact row list is single-column.
- Each row contains server identity and a horizontally scrollable GPU strip.
- Tapping a row or detail affordance opens a bottom sheet.
- Bottom sheet:
  - Uses the same content model as desktop detail.
  - Has an explicit close button.
  - Restores focus to the selected row on close.
  - Uses reduced-motion-safe slide/fade behavior.
- Avatar hover states must have tap/focus equivalents; mobile cannot rely on hover.

## 6. GPU slot and avatar states

### Slot states

- Available/no users:
  - Show exact slot label and a quiet available mark.
  - Do not show global or card-level free-count copy.
- One user:
  - Show one small avatar.
  - Tooltip/focus text includes the full username.
- Two users:
  - Show two overlapping avatars.
  - Tooltip/focus text includes both full usernames.
- Three or more users:
  - Show the first two avatars plus `+N`, where `N = total users - 2`.
  - Tooltip/focus text includes every username.
- Unknown/stale GPU metrics:
  - Preserve exact slot label if known.
  - Mark metrics stale/unknown without inventing availability.

### Avatar source order

1. Confirmed Linux username to Slack user mapping.
2. Slack profile image from cached `image_*` fields.
3. Stable initials fallback from Slack display/real name if mapped but image unavailable.
4. Stable initials fallback from Linux username if unmapped or Slack unavailable.

### Initials rules

- Deterministic per displayed identity.
- One or two characters, legible at small size.
- Use stable background color derived from username/Slack ID, with sufficient contrast.
- Do not use random colors on every refresh.

## 7. Interactions

- View switching:
  - Switching between Default and Compact preserves network scope.
  - Manual order is shared.
- Row selection:
  - Click/Enter/Space selects a row.
  - Selected row emphasis is subtle and persistent until another row is selected or detail is closed.
  - Selecting a row opens desktop detail or mobile bottom sheet.
- Full-name reveal:
  - Hover over a GPU avatar group shows all names for that GPU.
  - Focus on the group shows the same names.
  - Mobile tap/focus can show a popover or rely on bottom-sheet details.
- Keyboard:
  - Rows are reachable in DOM/manual order.
  - GPU groups inside rows are reachable if they expose hover/focus names.
  - Escape closes detail panel/sheet where appropriate.
- Refresh:
  - WebSocket/polling updates may change metrics and avatars in place.
  - Updates must not reorder rows.
- Slack profile refresh:
  - Profile cache updates replace initials with photos when available.
  - Cache misses or errors never block row rendering.

## 8. Data flow

### Current telemetry flow

1. Backend collectors gather GPU/system/storage data by server.
2. `get_current_state()` exposes current server state.
3. `/servers/status` returns a map of `ServerState` values and adds fallback unknown states for registered servers without telemetry.
4. WebSocket `/ws/metrics` pushes live updates.
5. Frontend stores normalize and merge server status with server catalog records.
6. Dashboard views render from the same `ServerState` and `GpuInfo` contracts.

### Compact enrichment flow

- Compact reads telemetry from the existing dashboard state.
- Linux usernames from `GpuInfo.users` are the source keys for identity previews.
- A backend-owned Slack profile cache and mapping service may provide optional display data keyed by Linux username.
- The frontend should treat Slack profile data as enrichment, not source of truth for occupancy.
- Occupancy state always comes from telemetry users on each GPU.

## 9. Backend mapping/cache/API boundaries

### Mapping model boundary

- Persist explicit records mapping `linux_username` to `slack_user_id`.
- Store admin confirmation metadata such as confirmer and timestamp if/when implementation plans define schema details.
- Exact-name suggestions are allowed but remain unconfirmed until admin approval.
- Suggestions must not silently change dashboard identity.

### Slack cache boundary

- Cache Slack user profiles periodically using backend-only Slack tokens.
- The cache should include Slack user ID, display/real name fields needed for labels, image URL fields needed for avatars, deleted/deactivated flag, and cache timestamp.
- Cache refresh must use pagination for `users.list`.
- Cache refresh failure preserves the last usable cache and records stale status for admin/debug visibility.

### API boundary

- Frontend must never receive Slack tokens.
- Frontend may receive a compact profile map or per-user identity enrichment containing only display-safe fields:
  - Linux username.
  - Confirmed Slack user ID when mapped.
  - Display name/full name.
  - Avatar URL if available.
  - Initials fallback.
  - Mapping/profile status.
- Telemetry endpoints remain usable without Slack configured.
- Slack profile endpoints must fail soft: empty enrichment plus fallback status is acceptable.

### Scope boundary

- `users:read` is required for default profile/avatar cache via `users.list`.
- `users:read.email` is not part of the default Compact design because email matching is intentionally avoided.
- If a later design chooses email matching, that feature must explicitly request `users:read.email` and document the privacy tradeoff.

## 10. Accessibility

- Target WCAG 2.2 AA where practical.
- Compact rows use semantic list/table-like structure with stable DOM order matching manual order.
- Each row has an accessible name including server name, status, network when relevant, and freshness.
- Each GPU slot has an accessible name including exact slot label, occupancy, all usernames, utilization integer percent, and memory integer `used/total GB`.
- Avatar images need useful alternative text or must be hidden when adjacent text/aria supplies names.
- `+N` must be announced as the hidden user count and must not hide the full name list from assistive tech.
- Tooltip/popover content is reachable by keyboard and does not vanish before users can read it.
- Detail panel/sheet has an accessible title based on server name.
- Mobile bottom sheet manages focus and close behavior.
- Reduced motion disables row/panel transition movement and hover lifts.
- Color is never the only indicator for availability, status, or selection.

## 11. Failure states

- Slack token missing: dashboard uses initials from Linux usernames; telemetry remains normal.
- Missing `users:read`: profile cache unavailable; show initials and admin-facing scope status.
- Slack API rate limited/unavailable: keep last usable cache when present; otherwise initials.
- Slack member deleted/deactivated: fall back to initials/name and do not hide telemetry user.
- Missing profile image: initials fallback.
- No mapping: Linux username initials and exact Linux username in hover/detail.
- Ambiguous exact-name suggestion: no automatic mapping; admin confirmation required.
- Telemetry stale: show stale server/GPU status from existing freshness model; do not blame Slack.
- WebSocket failure: preserve polling fallback behavior.
- Empty scope: show existing no-server/no-scope empty state adapted to Compact.

## 12. Testing and visual acceptance criteria

### Documentation acceptance for this branch

- `DESIGN.md` marks Compact as active while preserving Default.
- This spec includes product goal, IA, desktop/mobile layout, GPU/avatar states, interactions, data flow, backend boundaries, accessibility, failure states, non-goals, and wireframes.
- Official Slack docs are referenced for `users.list` and `users:read.email`.
- No frontend/backend source implementation occurs.
- Design documentation may be committed after review; no push, deployment, or production repo edit occurs.

### Future implementation visual checks

- Desktop 1440x1000 dark and light:
  - Header remains current and not hero-like.
  - Compact rows preserve manual order.
  - Each server is one dense row.
  - GPU slots show exact `G#` labels.
  - One/two/three-plus avatar rules render correctly.
  - Selected row has subtle emphasis.
  - Right panel shows all usernames, utilization integer percent, and integer memory.
- Mobile 390x844 dark and light:
  - Compact rows are readable.
  - GPU strip preserves slot order and labels.
  - Bottom sheet contains complete detail and closes accessibly.
- Interaction checks:
  - Keyboard can select rows and reveal names.
  - Hover/focus reveals full names.
  - Refresh updates do not reorder rows.
  - Slack fallback states do not block telemetry rendering.
- Preservation checks:
  - Default card dashboard still renders.
  - Server CRUD, notes, logs, debug, network scope, theme controls, and manual order still work.

## 13. Non-goals

- No implementation plan in this document.
- No frontend/backend source implementation in this design-only branch task.
- Design documentation may be committed after review; no push, deployment, or production repo edit.
- No replacement of the default dashboard.
- No auto-sort by availability or any other heuristic.
- No email matching by default.
- No sharing/export/public link feature.
- No giant hero or card-level free-count copy.
- No Slack dependency in telemetry collection or dashboard rendering.

## 14. ASCII wireframes

### Desktop

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ GPU Monitor   정상 · 10초 전 · 다음 새로고침  Internal [12] External [4] All [16]  View Manage ◐ │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Compact list (manual order)                                             Detail panel          │
│ ┌──────────────────────────────────────────────────────────────┐ ┌─────────────────────────┐ │
│ │ ● Poseidon 166.104.167.11:2203 · 8초 전                      │ │ Poseidon                │ │
│ │ G0 [AB]  G1 [CD][EF]  G2 [GH][IJ]+2  G3 [available]        > │ │ 정상 · 내부망 · 8초 전   │ │
│ └──────────────────────────────────────────────────────────────┘ │                         │ │
│ ┌──────────────────────────────────────────────────────────────┐ │ G0 42% 21/80 GB         │ │
│ │ ● Hinton 166.104.167.12:22 · 12초 전                         │ │   AB full.name          │ │
│ │ G0 [available]  G1 [KL]  G2 [available]  G3 [MN][OP]       > │ │                         │ │
│ └──────────────────────────────────────────────────────────────┘ │ G1 88% 67/80 GB         │ │
│ ┌──────────────────────────────────────────────────────────────┐ │   CD full.name          │ │
│ │ ! Turing 166.104.167.13:22 · 지연                             │ │   EF full.name          │ │
│ │ G0 [?]  G1 [?]                                               > │ │                         │ │
│ └──────────────────────────────────────────────────────────────┘ │ G2 76% 58/80 GB         │ │
│                                                                  │   GH full.name          │ │
│                                                                  │   IJ full.name          │ │
│                                                                  │   QR full.name          │ │
│                                                                  │   ST full.name          │ │
│                                                                  └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Mobile

```text
┌──────────────────────────────┐
│ GPU Monitor        Manage ◐  │
│ 정상 · 10초 전               │
│ [Internal 12] [External 4]   │
├──────────────────────────────┤
│ ● Poseidon · 8초 전          │
│ 166.104.167.11:2203          │
│ G0 [AB]  G1 [CD][EF]  G2 +2 │
│                              │
│ ● Hinton · 12초 전           │
│ G0 [available] G1 [KL]      │
│                              │
│ ! Turing · 지연              │
│ G0 [?] G1 [?]               │
├──────────────────────────────┤
│ Bottom sheet: Poseidon    ×  │
│ 정상 · 내부망 · 8초 전       │
│ G0 42% 21/80 GB             │
│   AB full.name              │
│ G1 88% 67/80 GB             │
│   CD full.name              │
│   EF full.name              │
│ G2 76% 58/80 GB             │
│   GH full.name              │
│   IJ full.name              │
│   QR full.name              │
│   ST full.name              │
└──────────────────────────────┘
```

## 15. Self-review notes

- Placeholder scan: no incomplete placeholder markers remain.
- Contradiction scan: Compact is active and separate; Default is preserved, not replaced.
- Scope scan: frontend/backend source implementation, implementation planning, pushes, deployment, production repo edits, sharing, auto-sort, and email matching are explicitly out of scope; design documentation may be committed after review.
- Ambiguity scan: server order, GPU numbering, avatar grouping, Slack fallback, detail placement, and telemetry non-blocking rules are explicit.
