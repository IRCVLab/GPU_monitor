# Dense Apple GPU monitor design spec

- Date: 2026-07-14
- Last refreshed: 2026-07-14
- Status: Active user-approved design contract for implementation planning.
- Repository/branch: `~/workspace/monitoring_v2_dev`, `feature/compact-gpu-dashboard`
- Scope: Active implementation contract for `~/workspace/monitoring_v2_dev`. Do not push, deploy, or touch `~/workspace/monitoring_v2`.
- Supersession: root `DESIGN.md` and this spec are the only active design contracts. All prior dashboard/header/compact specs and plans are historical where conflicting. User-facing card-dashboard name is `Full`, not `Default`.
- Exact theme/token source: `https://tweakcn.com/themes/cmr2flrsp000304ih46yj4y1b?p=marketing` (`Apple Liquid Glass`). The values below were extracted from the theme payload and are authoritative.
- Secondary design-language reference: Apple Human Interface Guidelines, `https://developer.apple.com/design/human-interface-guidelines/`. It informs restraint only and does not override the extracted tokens.

## 1. Product job

Researchers scan independent GPU servers that are not connected by a high-speed training network. The dashboard must help them find an empty server or exact empty GPU, identify users already occupying each GPU, and avoid mistaking advisory notes for actual telemetry occupancy.

## 2. Approved hybrid direction

- `B`: Full-card format/content hierarchy from the `cf70ad0` small-density baseline.
- `A`: adaptive header behavior with earlier hide/reveal, threshold near 30px, and layout-space reclamation.
- `C`: restrained Apple Liquid Glass visual language using the exact token export below.
- Current stack remains SvelteKit 5, TypeScript, Tailwind, and repo CSS layers. The generic bootstrap/reference design is inspiration only.

## 3. Global invariants

- Preserve the `Full` / `Compact` toggle.
- Preserve manually ordered `currentServers`; never auto-sort.
- Preserve exact GPU labels from telemetry: `G0`, `G1`, `G2`, and so on.
- Use integer GPU memory values only, such as `21/24 GB`.
- Keep Slack profiles, hard scheduling/locks, auto-sort, production edits, new dependencies, sharing/export, collector changes, and WebSocket payload changes out of scope.
- Keep existing immediate light/dark theme switching. Generic 600ms circular reveal is explicitly not required.

## 4. Full view requirements

- Full is the preserved server-card dashboard.
- Fix Full at the old `작게` density.
- Remove density setting UI, density cookie semantics, and layout-width preference semantics.
- Aim for three columns at 1440px framed width.
- Use dense masonry or masonry-equivalent placement while preserving manual order and DOM order.
- Follow the `cf70ad0` small-density card hierarchy: server identity first, health/status, exact GPU rows, compact secondary details.
- Use strong restrained emerald for utilization and restrained blue for memory.
- Show clear active/occupied `G#` cues without turning each GPU into a large tile.
- Keep system and memo/note content in a compact footer or compact expanded content.

## 5. Header exact no-overlap contract

- Status/freshness sits on the left.
- Network selector remains usable at every supported width.
- `View` contains mode (`Full` / `Compact`) and color theme.
- Sun/moon light-dark toggle sits far right.
- `Manage` includes registration, log access, debug access, and delete management.
- Expanded header participates in normal flow.
- On intentional down scroll, the reserved header block collapses while the header translates/fades.
- Scroll behavior uses `requestAnimationFrame`, direction tracking, and an approximate 30px threshold.
- Upward scroll restores the header.
- Desktop indicator dot is allowed only when an outer gutter exists; if present it uses a 12-16px top offset.
- On narrow widths, do not render a floating dot.
- Hover/focus detail temporarily reserves its own slim row or restores the header; it never overlays cards or list rows.
- Header surfaces and popovers must fit the viewport.
- Motion is 140-240ms and reduced-motion safe.

## 6. Compact availability-only contract

- Compact is a dense full-width availability matrix/list.
- Render servers in the same manual order as Full.
- Do not render an aside, placeholder, reserved second column, persistent giant detail rail, or persistent empty region when unselected.
- Desktop selected details are only a conditional temporary anchored popover/overlay that fits the viewport.
- Mobile selected details use a viewport-safe overlay/sheet.
- Do not show IP rows or freshness rows in the main Compact list.
- Do not require page or row horizontal scroll.
- Mobile wraps GPU groups/cells instead of using a horizontal strip.
- Free GPUs are strongly green.
- Occupied GPUs are neutral/blue with deterministic Linux-username initials.
- Unknown or stale telemetry is amber and never claims availability.
- Full usernames are available through hover and keyboard focus, with touch equivalent on mobile.
- Compact optimizes scan speed; richer system/memo inspection stays in Full unless exposed by a compact viewport-safe overlay.

## 7. Soft-hold exact YAGNI contract

- Implement soft holds as a backward-compatible Note extension only.
- Note fields: `kind 'memo'|'hold'`, `gpu_indices number[]`, existing `username`, `content`, `created_at`, and `expires_at`.
- Storage may add `notes.kind` default `'memo'` and `notes.gpu_indices` nullable JSON text.
- API serializes `gpu_indices` as `number[]`.
- Create request defaults to `kind='memo'` and `gpu_indices=[]`.
- Memo must have no GPU indices.
- Hold requires at least one unique non-negative integer GPU index.
- Normalize hold GPU indices ascending.
- Creation uses the existing note authentication path to verify Linux username/password.
- Cancellation is deletion through the existing owner/admin-authenticated `DELETE` path.
- Do not add `cancelled_at`, status, scheduler, overlap rejection, or hard exclusivity.
- Expired holds are cleaned/omitted by current expiry behavior.
- Near-expiry holds show a countdown.
- `Stale` refers to telemetry freshness, not a stale hold record.
- Stale/unknown telemetry shows a warning and never claims availability.
- Holds are advisory overlays only. Current telemetry remains truth for actual occupancy.
- Notes API/storage may change. Collector and WebSocket payloads may not change.
- Plain memo behavior and rendering remain.

## 8. Visual system and token guardrails

- Use exact light/dark tokens below.
- Do not invent standalone `surface-opacity` tokens.
- Do not invent gradient-stop tokens or decorative gradients from the reference export.
- Frosted opacity may use semantic color-mix as component treatment for header, popover, or selected surfaces; it is not an extracted token.
- Glass restraint applies only to header, popover, and selected surfaces.
- Operational density beats marketing whitespace.
- Typography uses native system stack, tabular numerals, and `letter-spacing 0em`.
- Base spacing is `0.25rem`.
- Motion is restrained at 140-240ms.

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

## 9. Identity model

- Linux usernames are the identity source for this branch.
- Occupancy belongs to exact GPU slots.
- Deterministic initials derive from username strings.
- One user shows one initials mark.
- Two users show two overlapping marks.
- Three or more users show two marks plus `+N`.
- Hover/focus reveals all usernames.
- Slack profiles, Slack avatars, email matching, profile cache, and Linux-to-Slack mapping are non-goals.

## 10. Accessibility and responsive acceptance

- Keyboard reaches header controls, network selector, View/theme controls, Manage, Full cards, Compact rows, GPU groups, notes, overlays, and dialogs.
- Color is paired with label, icon, shape, or text.
- Compact username reveal works on hover and focus; mobile has tap/focus equivalent.
- Selected row state is visible and announced.
- Popovers/overlays fit the viewport, close predictably, and return focus.
- Mobile wraps Compact GPU groups and has no page/row horizontal scroll.

## 11. Loading, stale, error, and empty states

- Loading preserves final layout rhythm.
- Empty states distinguish no registered servers, no servers in selected network scope, and registered servers without telemetry.
- Stale telemetry is explicit and amber.
- Unknown GPU metrics do not invent availability.
- Error state retains last-known data when safe and offers retry.
- Header transport status is separate from per-server collector status.

## 12. Acceptance tests and visual QA contract

- Static verification for documentation changes: `git diff --check`.
- Future implementation static checks: frontend type/Svelte checks and production build.
- Soft-hold acceptance tests:
  - Exact `G#` hold create succeeds with normalized `gpu_indices`.
  - Plain memo creation/rendering regression passes with `kind='memo'` and no GPU indices.
  - Owner delete succeeds through existing authenticated `DELETE`.
  - Admin delete succeeds through existing admin-authenticated `DELETE`.
  - Non-owner delete returns 403.
  - Expired hold is omitted by current expiry behavior.
  - Stale telemetry warning appears and never claims availability.
  - Collector/WebSocket payload contract has no diff.
- Visual QA:
  - Compare Full against `cf70ad0` small-density screenshot/behavior baseline.
  - Verify dark and light at 1440px framed width.
  - Verify mobile wrapping and no horizontal page scroll.
  - Verify header hide/reveal around the 30px direction threshold, layout-space reclamation, no card overlap, and upward-scroll restore.
  - Verify Compact has no persistent rail, aside, placeholder, reserved second column, or unselected empty detail region.

## 13. No-ambiguity self-review

- [x] No placeholder markers remain in this active spec.
- [x] Active contract supersession is absolute.
- [x] `Full` is the user-facing card-view name.
- [x] Soft-hold scope is backward-compatible YAGNI Note extension only.
- [x] Compact detail uses temporary overlays only and reserves no unselected rail/column.
- [x] Header no-overlap behavior is explicit.
- [x] Token guardrails include shadow spread, spacing, and letter spacing, and forbid invented opacity/gradient tokens.
