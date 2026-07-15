# Quiet Rack GPU Monitor Design Spec

- Date: 2026-07-15
- Status: Active user-approved design contract.
- Repository/branch: `~/workspace/monitoring_v2_dev`, `feature/compact-gpu-dashboard`.
- Scope: Development branch only. Do not edit, rebuild, restart, or deploy `~/workspace/monitoring_v2`.
- Supersession: This spec supersedes earlier Compact layout, color, detail-panel, mobile wrapping, and Full metric-color rules where they conflict.

## 1. Product job

The primary user is a researcher choosing an independent GPU server for training. In one glance, the dashboard must answer:

1. Which server has a trustworthy available GPU?
2. Which exact `G#` is available?
3. Who occupies the unavailable GPUs?

Operational diagnosis, historical uptime, and rich system telemetry are secondary. Compact is an availability board; Full is the detailed inspection surface.

## 2. Design interpretation

“Apple-like” and “Jonathan Ive-like” mean disciplined reduction, not decorative imitation:

- **Inevitability:** every visible element has one clear job and sits where comparison expects it.
- **Material logic:** occupied GPUs feel filled; available GPUs feel like open apertures.
- **Progressive disclosure:** the first surface answers availability, while detail stays available without permanently consuming space.
- **Continuity:** state and layout changes preserve the user’s spatial map.
- **Quiet precision:** typography, alignment, hairlines, and spacing do more work than glow, gradients, or badges.
- **Nudge, not announcement:** the layout leads the eye toward usable capacity without a large textual free-count headline.

Reject decorative glass on data cards, glow around normal state, oversized pills, variable-width GPU cells, repeated `G#` and `정상` labels, large Compact inspectors or sheets, multiple competing chart colors, and marketing whitespace that reduces visible servers.

## 3. Global hierarchy

### Header

- Desktop expanded height targets `52-56px`.
- Mobile expanded header may use two compact rows, targeting `88-96px` total.
- Intentional downward scrolling removes both the header content and its reserved layout height.
- The collapsed state leaves one viewport-safe health/refresh indicator aligned to the left content gutter.
- `GPU Monitor` is identity, not the primary data headline.
- Network scope remains a restrained segmented control.
- `View` owns Full/Compact, Full-only `Grid` / `Masonry`, and color theme. Full density stays fixed at the old small density.
- Light/dark remains the separate far-right sun/moon control.

### Main surfaces

- Full: dense server cards with usernames, utilization, memory, system, memo, and hold information.
- Compact: one fixed-column availability rack with no operational metadata in primary rows.

## 4. Compact availability matrix

### 4.1 Board geometry

- One bordered surface, `14-16px` radius, no decorative shadow.
- Maximum useful width targets `880-960px`; center it inside the page so GPU cells never stretch into large bars.
- One shared GPU header row labels `G0-G7` once.
- Server rows use the same grid template as the header.
- Reserve eight columns for the current hardware fleet. Missing hardware renders as an absent placeholder and never stretches existing cells.
- If any hardware index exceeds `G7`, a global bank selector (`G0-G7`, `G8-G15`, and so on) is mandatory. A bank change preserves server order and one row per server. No GPU index is silently hidden.

Grid target:

```css
grid-template-columns:
  clamp(72px, 18vw, 132px)
  repeat(8, minmax(22px, 1fr));
```

- Desktop row height: `34-38px`.
- Mobile row height: `40-42px`, making the row the touch target.
- Gap between GPU cells: `3px` desktop and `2px` mobile.
- Desktop GPU cell height: `26-28px`.
- Mobile GPU cell height: `24-26px`.
- GPU cell radius: `6-8px`, smaller than the board radius.

### 4.2 Server identity

- Server name is the only visible row text outside GPU cells.
- Use one line with ellipsis.
- Normal status is a `5-6px` green dot without visible `정상` text.
- Degraded/offline/unknown use amber/red/muted dots and concise text only when the exception requires explanation.
- Preserve manual server order exactly in Full and Compact.

### 4.3 Four-state GPU model

1. **Available**
   - Hardware exists, server telemetry is online and fresh, and no users are attached.
   - Dark/open interior.
   - Highest-clarity accent outline among normal GPU states.
   - Small hollow aperture mark in the center.
   - No large text or filled success color.

2. **Occupied**
   - One or more Linux users are attached.
   - Filled with the selected theme accent, but at restrained saturation so it does not overpower available outlines.
   - Dark high-contrast initials, maximum two initials plus `+N` when needed.
   - No per-user rainbow colors.

3. **Unknown**
   - Hardware exists but server state or telemetry freshness cannot support an availability claim.
   - Muted surface with a subtle diagonal texture or centered neutral mark.
   - Never uses the available outline.

4. **Absent**
   - The server has no hardware at that index.
   - Transparent/quiet placeholder that preserves column alignment.
   - Not interactive and not announced as a GPU.

### 4.4 Nudge hierarchy

The user must perceive availability without reading a count:

- A server with at least one available GPU receives a restrained `2px` accent rail at the row’s left edge.
- Available cells use the clearest normal-state outline and aperture mark.
- Occupied cells create a filled material field; available cells read as deliberate holes in that field.
- Fixed columns let the eye compare one GPU index vertically without restarting each row.
- Normal status remains smaller and spatially separated from GPU state.
- Visible free-count text is prohibited in primary Compact UI. Accessible labels may contain counts.

### 4.5 Compact interaction

- Compact cells are passive visual marks on mobile; the full row is the touch target.
- Desktop occupied cells support hover/focus disclosure.
- Tap/click/focus opens a micro-popover only when additional identity information is useful.
- Popover width: `180-220px`; maximum height: `120px`.
- Popover content: server name, exact `G#`, state, and full usernames for occupied GPUs.
- No server inspector, side rail, layout-shifting detail column, large bottom sheet, IP address, freshness row, system metrics, or memo history.
- Escape/outside click closes the popover and focus returns to the initiating row/cell.
- An explicit `Full에서 보기` action may switch to Full and focus the same server when deeper information is required.

### 4.6 Holds

- Compact availability remains telemetry truth; a soft hold does not convert a free GPU into occupied.
- If active hold data is already present, show only a `2px` amber corner notch.
- The notch never replaces the available/occupied/unknown state.
- Full remains the authoritative surface for hold owner, memo, and expiry.

## 5. Full server cards

### 5.1 Card shell

- Keep dense ordered masonry and the existing fixed small density.
- Major card radius may remain rounded, but internal sections use hairlines and spacing rather than nested boxes.
- Hover changes elevation by at most `1px` or a slight shadow adjustment.
- Replace whole-card drag affordance with a dedicated drag handle so controls remain unambiguous.

### 5.2 Card header

- Server name is primary.
- Normal state uses a small dot; status text is reserved for degraded/offline/unknown.
- Host and refresh share one restrained metadata line.
- Edit control is hidden until hover/focus.
- Do not add a prominent textual free-count badge.

### 5.3 GPU rows

- Preserve separate usernames because multiple users can occupy one GPU.
- Available index: dark surface, selected-theme outline/text.
- Occupied index: selected-theme fill, dark text.
- Unknown index: muted neutral treatment.
- Use one selected-theme hue for both Util and Mem; distinguish them through label, width, opacity, and track treatment rather than competing green/blue hues.
- Give Mem the larger flexible width because memory values and capacity comparison need more horizontal room.
- GPU memory is integer-only in every display.
- For an available idle GPU, metrics remain visible but visually quiet; zero bars must not dominate.

## 6. System footer

### Collapsed

- Keep one `26-30px` row.
- Replace prose-like punctuation with fixed micro-columns: `CPU`, `RAM`, `GPU`, `Disk`.
- Use tabular values and align them consistently across cards.
- Normal values stay muted; thresholds alone receive amber/red emphasis.

### Expanded

- CPU/RAM/GPU power/Disk summary uses a compact two-column or four-column metric matrix.
- GPU hardware items and storage mount rows target `24-26px` height.
- Reduce nested boxes, repeated headings, and separators.
- Expanded content remains dense enough that it does not visually become a second dashboard inside a card.

## 7. Memo and soft hold

### Collapsed

- One line only.
- Hold scope appears first as a small amber `G#` cue.
- Then show owner and memo content.
- Expiry remains right-aligned, tabular, and compact.
- Empty memo state is intentionally quiet.

### Expanded/composer

- Memo and hold share one composer; GPU selection changes the note kind.
- Target maximum three compact rows: GPU scope, identity/content, expiry/submit.
- Explanatory copy appears only for stale telemetry or abnormal server status.
- An active hold cue also appears directly on the affected Full GPU row.
- Hold remains advisory and never changes telemetry occupancy.

## 8. Header and refresh indicator

- The refresh animation is a continuous visual clock, independent from request completion.
- Center dot uses a slow breathing cycle.
- Satellite orbit represents the nominal ten-second cadence and never snaps at completion.
- Requests start on cadence in parallel with the visual cycle.
- Ordinary refreshing never inserts text or shifts adjacent controls.
- Only persistent delay/failure displays text.
- Collapsed indicator is inset from both top and left viewport edges and may never clip outside the browser.
- Hover/focus panel shows health/freshness and network scope without covering dashboard content.

## 9. Theme and color semantics

- Exact extracted theme tokens remain authoritative.
- Selected `colorTheme` provides one GPU/metric accent.
- Green is reserved for normal server health where practical.
- Amber means degraded, stale, threshold warning, or advisory hold.
- Red means offline, destructive action, or critical threshold.
- Do not communicate state with color alone; pair color with fill/outline/texture/label semantics.

## 10. Motion

- Motion preserves continuity; it does not decorate.
- Header collapse/reveal: `160-180ms`, transform and opacity only.
- Compact cell state: `120-160ms` background, border, and color transition.
- Full metric values: linear width interpolation without end-of-cycle snap.
- Grid/Masonry transition: existing FLIP position continuity.
- Compact rows do not bounce, scale, or glow.
- Reduced motion removes nonessential translation and scaling.

## 11. Responsive acceptance

- `1440x900`: Compact matrix is centered and cells remain deliberate rather than stretching across the full page.
- `1024x768`: same fixed-column model and one server per row.
- `390x844`: one server per row, eight GPU columns, no GPU wrapping, no horizontal page/row scroll.
- The current fleet of up to 11 visible servers must remain visible without ordinary Compact page scrolling at `390x844`; larger future fleets may scroll vertically without changing one-row geometry.
- Mobile GPU marks are passive; the row provides the touch target.
- Header and collapsed indicator stay completely inside the viewport.

## 12. Test and visual QA contract

- Contract tests must fail first for shared fixed GPU columns, absent placeholders, exactly four base states, stale-to-unknown mapping, normal status text removal, no inspector/sheet, mobile one-row layout, Full/Compact server order preservation, conditional Full-only Grid/Masonry controls, mandatory `G8-G15` bank behavior, and unified metric color roles.
- Run frontend unit/contract tests, Svelte check, production build, and existing backend tests.
- Playwright visual QA covers dark/light at `1440x900`, `1024x768`, and `390x844`.
- Verify unknown/offline servers, 2/4/8 GPU servers, duplicate initials, active holds, and a synthetic `G8+` bank.
- Console must contain no errors or warnings caused by the redesign.

## 13. Completion criteria

- Researchers can visually identify rows containing available GPUs without reading a free-count label.
- Exact available `G#` cells align vertically across servers.
- Compact never becomes a miniature Full card or a layout-shifting inspector experience.
- Full remains dense but its hierarchy clearly prioritizes server, GPU state, user, and metrics in that order.
- System and memo expansions add information without destroying the dashboard’s density.
- Header motion reclaims space and the collapsed indicator never overlaps or clips.
- Every change preserves manual server order and leaves the live repository/processes untouched.
## 14. Deep refinement acceptance

- Full card availability is hinted by one narrow accent rail only when at least one GPU is truly available.
- The rail never includes free-count text and never reorders cards.
- Available zero telemetry is quieter than occupied telemetry, while exact values remain readable.
- Compact occupied disclosure uses two-column G# and full-username rows without redundant 사용 중 text.
- The Full에서 보기 action survives pointer focus changes, remains keyboard-operable, and focuses the same server in Full mode.
- The View trigger exposes the current Full or Compact mode before opening.
- Desktop header content and card outer edges share one exact horizontal gutter.
- The collapsed indicator lane uses a restrained semantic mask so passing card content never visibly collides with the status dot or panel.
- Indicator lane synchronization runs only for actual compact, visibility, or panel geometry changes and never fights normal scrolling.
