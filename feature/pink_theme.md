# Rose Quartz Theme Spec

## Goal

Add `Rose Quartz` as a third independent dashboard theme alongside the existing `dark` and `light` themes.

The theme must feel compact, calm, and Apple-like without becoming candy-like or decorative. It should read as a real operating theme for monitoring, not a novelty skin.

## Core Decision

`Rose Quartz` is a full theme, but it is **not** a new meaning system.

- Theme atmosphere becomes rose-tinted.
- Operational semantics stay stable across all themes.
- The dashboard should still scan the same way for status, failure, warning, and activity.

## Visual Rules

### 1. Theme atmosphere

Use warm rose-gray neutrals for page chrome and subtle surface tinting.

- Page background: soft rose-gray wash
- Header: frosted porcelain / blush glass
- Cards: nearly white surfaces with a faint warm tint
- Panels and insets: slightly deeper rose-gray separation

The page can feel tinted; the data surfaces must remain cleaner and brighter than the page chrome.

### 2. Accent policy

Use rose for theme identity, not for high-risk operational semantics.

Rose-toned:
- page glow and chrome tint
- header title block edge
- tab active background
- view/theme selector active background
- focus ring
- quiet hover fills
- non-semantic dividers and small decorative edges

Preserve semantic colors:
- green / emerald for healthy online state
- amber for degraded / warning
- red for offline / destructive / error
- blue for informational identity accents already used in dense data zones

### 3. Semantic preservation

The following should **not** be recolored to rose:

- online / degraded / offline badges
- log severity colors
- destructive actions
- warning states
- GPU occupancy cues that rely on existing semantics

The following should remain blue informational accents:

- GPU user names
- memo author names
- info-level emphasis text

`Live` can adopt a rose-toned surface, but its inner hierarchy still needs to read as monitoring metadata, not decoration.

### 4. Density and restraint

This theme must stay compact.

- No heavy gradients
- No neon glow
- No glossy candy effects
- No full-card pink fills
- No large tinted pills that overpower content

The intended feeling is `quiet precision`, not `cute`.

## Palette

Base tokens for the first implementation:

- `rq-bg`: `#F6F2F5`
- `rq-surface`: `#FFF9FB`
- `rq-surface-muted`: `#F4EDF1`
- `rq-border`: `#D9CDD4`
- `rq-accent`: `#B86C87`
- `rq-accent-soft`: `rgba(184, 108, 135, 0.10)`
- `rq-text-strong`: `#241C22`
- `rq-text-muted`: `rgba(36, 28, 34, 0.58)`

These are starting tokens, not a license to tint every component equally.

## Interaction Design

The current binary theme toggle is no longer sufficient.

Replace it with a compact 3-state theme selector in the header:

- `Dark`
- `Light`
- `Rose`

Requirements:

- stays compact in the existing header control area
- works with keyboard and click
- active state is immediately visible
- persists in the existing theme cookie

Cycling blindly through 3 themes with a single icon button is not acceptable because it hides state and creates friction.

Naming rule:

- internal spec/design name: `Rose Quartz`
- user-facing control label: `Rose`

## Persistence

Theme remains cookie-backed.

- Cookie key stays `theme`
- Allowed values become `dark | light | rose`
- Unknown values fall back to `dark`
- Existing users with `dark` or `light` cookies keep their current theme unchanged

Initial paint should also respect the stored theme as closely as the current shell allows.
If the first-paint class handling needs adjustment to avoid an incorrect dark flash before hydration, that is in scope.

## Scope

### In scope

- Add third theme state to the theme store
- Add rose theme class handling on `html`
- Add rose token block in global CSS
- Update header theme control for 3 choices
- Restyle the main dashboard surfaces for rose theme
- Restyle dashboard-adjacent form/modal surfaces only if they inherit from the global theme shell and otherwise look visually broken next to the dashboard
- Keep log/status semantics consistent with dark/light
- Preserve cookie persistence

### Out of scope

- Rebranding Slack output
- Changing operational meaning colors
- Reworking layout density or card structure solely for this theme
- New illustration or mascot-like decorative assets

## Files Expected To Change

Frontend only.

- `frontend/src/lib/stores/theme.ts`
- `frontend/src/routes/+page.svelte`
- `frontend/src/app.css`
- potentially `frontend/src/app.html` if initial theme class handling needs extension

## Verification

Minimum acceptance for implementation:

- `npm run check`
- `npm run build`
- manual visual check in all three themes
- cookie persistence confirmed across reload
- no loss of readability in GPU cards, memo preview, tabs, and logs

## Risks To Avoid

- making the theme too saturated
- recoloring semantics into rose
- tinting cards so much that data hierarchy weakens
- reducing text contrast in memo, logs, or metadata
- adding a 3-theme control that is visually louder than the dashboard content
