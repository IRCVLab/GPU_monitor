# Dense Apple GPU Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Full the dense Apple-style GPU dashboard, keep Compact availability-only, and tighten header and card density without changing telemetry, manual order, or backend contracts.

**Architecture:** Put the active Apple Liquid Glass tokens in global CSS once, keep `dashboardView` storage semantics unchanged, and split the work into four small UI changes plus one browser QA pass: label and theme cleanup, Full card density, header collapse, and Compact overlay behavior. The page stays the orchestration layer; local components keep their own markup and CSS.

**Tech Stack:** SvelteKit 5, Svelte 5 runes, TypeScript, CSS layers, Node 24 `node:test`, `npm run check`, `npm run build`, the remote `run_development.sh` stack, the exact tunnel `ssh -4 -N -L 15175:127.0.0.1:5175 -p 2200 ircv@166.104.167.11`, and the local Playwright CLI wrapper at `/Users/shchoi/.codex/skills/playwright/scripts/playwright_cli.sh`.

## Global Constraints
- Remote repo only: every shell command uses `cd ~/workspace/monitoring_v2_dev`.
- Frontend-only change set.
- Preserve manual `currentServers` ordering, telemetry payloads, and backend contracts.
- No new dependencies, push, or deployment.
- No density cookies, layout-width preferences, or second layout mode beyond Full and Compact.
- No CSS zoom.
- `Full` is the user-facing card-dashboard name; `Default` must disappear from the page menu.
- Compact is availability-only and must not keep a persistent rail, aside, or placeholder column.
- Visual-only Svelte/CSS work uses browser assertions and screenshots, not source-contract tests that already pass current code.
- Pure helper tests are only for new pure helpers.

## Exact Theme Token Contract
Use the active DESIGN token export exactly. `:root` defaults to dark; `html.dark` mirrors `:root`; `html.light` uses the light values. Keep `html.rose` untouched in this plan.

`html, body` font stack must start with `-apple-system` and must not include `Inter`.

```css
@layer base {
  :root {
    color-scheme: dark;
    --background: #090b0f;
    --foreground: #f0f2f4;
    --card: #13161b;
    --card-foreground: #f0f2f4;
    --popover: #1a1d22;
    --popover-foreground: #f0f2f4;
    --primary: #3a8cff;
    --primary-foreground: #040609;
    --secondary: #1c2024;
    --secondary-foreground: #d9dfe5;
    --muted: #181b1f;
    --muted-foreground: #8f9aa4;
    --accent: #152946;
    --accent-foreground: #a5d0ff;
    --destructive: #ff515a;
    --destructive-foreground: #ffffff;
    --border: #26292e;
    --input: #26292e;
    --ring: #3a8cff;
    --chart-1: #3a8cff;
    --chart-2: #00b793;
    --chart-3: #9b61ea;
    --chart-4: #ff7527;
    --chart-5: #fb3a7f;
    --sidebar: #0f1216;
    --sidebar-foreground: #f0f2f4;
    --sidebar-primary: #3a8cff;
    --sidebar-primary-foreground: #040609;
    --sidebar-accent: #152946;
    --sidebar-accent-foreground: #a5d0ff;
    --sidebar-border: #212429;
    --sidebar-ring: #3a8cff;
    --radius: 1.5rem;
    --shadow-color: #000000;
    --shadow-opacity: 0.45;
    --shadow-blur: 40px;
    --shadow-spread: 0px;
    --shadow-offset: 0 4px;
    --spacing: 0.25rem;
    --letter-spacing: 0em;

    --surface: var(--background);
    --elevated: var(--card);
    --text: var(--foreground);
    --surface-foreground: var(--foreground);
    --surface-muted: var(--muted);
    --surface-muted-foreground: var(--muted-foreground);
    --surface-border: var(--border);
    --surface-ring: var(--ring);

    --ops-bg: var(--background);
    --ops-fg: var(--foreground);
    --ops-card: var(--card);
    --ops-popover: var(--popover);
    --ops-primary: var(--primary);
    --ops-primary-fg: var(--primary-foreground);
    --ops-secondary: var(--secondary);
    --ops-secondary-fg: var(--secondary-foreground);
    --ops-muted: var(--muted);
    --ops-muted-fg: var(--muted-foreground);
    --ops-accent: var(--accent);
    --ops-accent-fg: var(--accent-foreground);
    --ops-danger: var(--destructive);
    --ops-danger-fg: var(--destructive-foreground);
    --ops-border: var(--border);
    --ops-input: var(--input);
    --ops-ring: var(--ring);
    --shadow: 0 4px 40px rgb(0 0 0 / 0.45);
    --ops-shadow: var(--shadow);
  }

  html.dark {
    color-scheme: dark;
    --background: #090b0f;
    --foreground: #f0f2f4;
    --card: #13161b;
    --card-foreground: #f0f2f4;
    --popover: #1a1d22;
    --popover-foreground: #f0f2f4;
    --primary: #3a8cff;
    --primary-foreground: #040609;
    --secondary: #1c2024;
    --secondary-foreground: #d9dfe5;
    --muted: #181b1f;
    --muted-foreground: #8f9aa4;
    --accent: #152946;
    --accent-foreground: #a5d0ff;
    --destructive: #ff515a;
    --destructive-foreground: #ffffff;
    --border: #26292e;
    --input: #26292e;
    --ring: #3a8cff;
    --chart-1: #3a8cff;
    --chart-2: #00b793;
    --chart-3: #9b61ea;
    --chart-4: #ff7527;
    --chart-5: #fb3a7f;
    --sidebar: #0f1216;
    --sidebar-foreground: #f0f2f4;
    --sidebar-primary: #3a8cff;
    --sidebar-primary-foreground: #040609;
    --sidebar-accent: #152946;
    --sidebar-accent-foreground: #a5d0ff;
    --sidebar-border: #212429;
    --sidebar-ring: #3a8cff;
    --radius: 1.5rem;
    --shadow-color: #000000;
    --shadow-opacity: 0.45;
    --shadow-blur: 40px;
    --shadow-spread: 0px;
    --shadow-offset: 0 4px;
    --spacing: 0.25rem;
    --letter-spacing: 0em;

    --surface: var(--background);
    --elevated: var(--card);
    --text: var(--foreground);
    --surface-foreground: var(--foreground);
    --surface-muted: var(--muted);
    --surface-muted-foreground: var(--muted-foreground);
    --surface-border: var(--border);
    --surface-ring: var(--ring);

    --ops-bg: var(--background);
    --ops-fg: var(--foreground);
    --ops-card: var(--card);
    --ops-popover: var(--popover);
    --ops-primary: var(--primary);
    --ops-primary-fg: var(--primary-foreground);
    --ops-secondary: var(--secondary);
    --ops-secondary-fg: var(--secondary-foreground);
    --ops-muted: var(--muted);
    --ops-muted-fg: var(--muted-foreground);
    --ops-accent: var(--accent);
    --ops-accent-fg: var(--accent-foreground);
    --ops-danger: var(--destructive);
    --ops-danger-fg: var(--destructive-foreground);
    --ops-border: var(--border);
    --ops-input: var(--input);
    --ops-ring: var(--ring);
    --shadow: 0 4px 40px rgb(0 0 0 / 0.45);
    --ops-shadow: var(--shadow);
  }

  html.light {
    color-scheme: light;
    --background: #f4f5f7;
    --foreground: #0c121a;
    --card: #ffffff;
    --card-foreground: #0c121a;
    --popover: #ffffff;
    --popover-foreground: #0c121a;
    --primary: #297cef;
    --primary-foreground: #ffffff;
    --secondary: #e9ebee;
    --secondary-foreground: #222933;
    --muted: #eceff1;
    --muted-foreground: #565e69;
    --accent: #d9e6f9;
    --accent-foreground: #002c78;
    --destructive: #ee343b;
    --destructive-foreground: #ffffff;
    --border: #dbdee2;
    --input: #e2e5e8;
    --ring: #297cef;
    --chart-1: #297cef;
    --chart-2: #00a381;
    --chart-3: #864ad2;
    --chart-4: #f3680f;
    --chart-5: #ec2773;
    --sidebar: #eceff1;
    --sidebar-foreground: #0c121a;
    --sidebar-primary: #297cef;
    --sidebar-primary-foreground: #ffffff;
    --sidebar-accent: #d9e6f9;
    --sidebar-accent-foreground: #002c78;
    --sidebar-border: #dbdee2;
    --sidebar-ring: #297cef;
    --radius: 1.5rem;
    --shadow-color: #4e5661;
    --shadow-opacity: 0.10;
    --shadow-blur: 28px;
    --shadow-spread: 0px;
    --shadow-offset: 0 2px;
    --spacing: 0.25rem;
    --letter-spacing: 0em;

    --surface: var(--background);
    --elevated: var(--card);
    --text: var(--foreground);
    --surface-foreground: var(--foreground);
    --surface-muted: var(--muted);
    --surface-muted-foreground: var(--muted-foreground);
    --surface-border: var(--border);
    --surface-ring: var(--ring);

    --ops-bg: var(--background);
    --ops-fg: var(--foreground);
    --ops-card: var(--card);
    --ops-popover: var(--popover);
    --ops-primary: var(--primary);
    --ops-primary-fg: var(--primary-foreground);
    --ops-secondary: var(--secondary);
    --ops-secondary-fg: var(--secondary-foreground);
    --ops-muted: var(--muted);
    --ops-muted-fg: var(--muted-foreground);
    --ops-accent: var(--accent);
    --ops-accent-fg: var(--accent-foreground);
    --ops-danger: var(--destructive);
    --ops-danger-fg: var(--destructive-foreground);
    --ops-border: var(--border);
    --ops-input: var(--input);
    --ops-ring: var(--ring);
    --shadow: 0 2px 28px rgb(78 86 97 / 0.10);
    --ops-shadow: var(--shadow);
  }

  html, body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    letter-spacing: var(--letter-spacing);
  }
}
```

## Exact File Map
- Task 1: `frontend/src/app.css`, `frontend/src/lib/stores/dashboardPrefs.ts`, `frontend/src/lib/utils/dashboardViewLabel.ts`, `frontend/src/lib/utils/dashboardViewLabel.test.ts`, `frontend/src/routes/+page.svelte`, `frontend/src/routes/page-view.contract.test.ts`
- Task 2: `frontend/src/routes/+page.svelte`, `frontend/src/lib/components/ServerCard.svelte`, `frontend/src/lib/components/GpuBar.svelte`, `frontend/src/lib/styles/monitor-dashboard.css`, `frontend/src/lib/styles/monitor-cards.css`
- Task 3: `frontend/src/lib/utils/headerVisibility.ts`, `frontend/src/lib/utils/headerVisibility.test.ts`, `frontend/src/routes/+page.svelte`, `frontend/src/lib/styles/monitor-dashboard.css`
- Task 4: `frontend/src/lib/components/CompactDashboard.svelte`, `frontend/src/lib/components/CompactServerRow.svelte`, `frontend/src/lib/components/CompactServerDetail.svelte`, `frontend/src/lib/styles/monitor-compact.css`, `frontend/src/routes/+page.svelte`
- Task 5: browser QA only, no repo source edits

## Task Dependencies
1. Task 1 lands first so the visible `Full` label helper and token contract are stable.
2. Task 2 lands next so Full card density can be measured before the header and Compact shifts.
3. Task 3 lands before final QA so the scroll threshold and safe gutter indicator are stable.
4. Task 4 lands after the header contract because Compact selection behavior depends on the page shell.
5. Task 5 runs last and must read the combined UI state.

### Task 1: Normalize the view label and theme token surface

**Interfaces**
- `type DashboardView = 'default' | 'compact'`
- `function dashboardViewLabel(view: DashboardView): 'Full' | 'Compact'`
- `function readDashboardView(): DashboardView`
- `function setDashboardView(value: DashboardView): void`
- `const dashboardView: Writable<DashboardView>`

**Focused implementation**
- Keep cookie storage values as `default` and `compact`.
- Render the view menu with `dashboardViewLabel('default')` and `dashboardViewLabel('compact')` so the page shows `Full` and `Compact` without a literal `Default`.
- Define the theme token block in `app.css` with the exact tokens above and the native font stack starting with `-apple-system`.

**Exact helper snippet**
```ts
export type DashboardView = 'default' | 'compact';

export function dashboardViewLabel(view: DashboardView): 'Full' | 'Compact' {
  return view === 'compact' ? 'Compact' : 'Full';
}
```

**Exact tests**
```ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { dashboardViewLabel } from './dashboardViewLabel.ts';

test('maps stored dashboard values to visible labels', () => {
  assert.equal(dashboardViewLabel('default'), 'Full');
  assert.equal(dashboardViewLabel('compact'), 'Compact');
});
```

```ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');

test('page menu uses the helper and never renders Default', () => {
  assert.match(pageSource, /dashboardViewLabel/);
  assert.doesNotMatch(pageSource, /\bDefault\b/);
});
```

**RED / GREEN**
- RED: helper test fails until the new helper exists; page source contract fails until `Default` is removed.
- GREEN: helper test passes and `npm run check` stays clean.

**Remote commands**
```bash
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && node --experimental-strip-types --test src/lib/utils/dashboardViewLabel.test.ts src/routes/page-view.contract.test.ts'
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run check'
```

**Commit**
```bash
git add frontend/src/app.css frontend/src/lib/stores/dashboardPrefs.ts frontend/src/lib/utils/dashboardViewLabel.ts frontend/src/lib/utils/dashboardViewLabel.test.ts frontend/src/routes/page-view.contract.test.ts frontend/src/routes/+page.svelte
git commit -m "feat: normalize dashboard labels and theme tokens"
```

### Task 2: Tighten Full density and GPU cues around the Apple baseline

**Interfaces**
- `interface ServerCardProps { server: ServerState; onEdit?: (server: ServerState) => void; showNetwork?: boolean; }`
- `interface GpuBarProps { gpu: GpuInfo; }`

**Focused implementation**
- Change the dashboard grid min width to `22rem` so 1440px frames resolve to three columns.
- Keep the existing masonry behavior and manual order.
- Remove any temptation to use CSS zoom or scale.
- Keep the semantic markup, but make the active `G#` cue visibly filled.
- Use `var(--chart-2)` for utilization fills with no inactive desaturation.
- Use `var(--chart-1)` for memory fills.
- Make the system and memo footer paddings and gaps compact.

**Exact page snippet**
```ts
const serverGridStyle = '--monitor-dashboard-card-min: 22rem;';
```

**Focused CSS replacement snippet**
```css
.monitor-dashboard-grid {
  --monitor-dashboard-card-min: 22rem;
  --monitor-dashboard-masonry-row: 8px;
  gap: 0.9rem;
}

.monitor-card {
  min-width: 22rem;
}

.monitor-card__gpu-list {
  gap: 0.55rem;
  padding: 0 0.9rem 0.9rem;
}

.monitor-card__footer {
  gap: 0.5rem;
  padding: 0.75rem 0.9rem 0.9rem;
}

.monitor-gpu-row[data-active='true'] .monitor-gpu-row__index {
  background: var(--chart-2);
  border-color: var(--chart-2);
  color: #040609;
}

.monitor-gpu-metric__fill--util {
  background: var(--chart-2);
}

.monitor-gpu-metric__fill--memory {
  background: var(--chart-1);
}
```

**RED / GREEN browser assertions**
- RED before implementation:
  - `document.querySelector('[style*="--monitor-dashboard-card-min: 24rem"]')` exists on the page shell; the current code should instead set `--monitor-dashboard-card-min: 22rem`.
  - `getComputedStyle(document.querySelector('.monitor-dashboard-grid')).getPropertyValue('--monitor-dashboard-card-min').trim()` is not `22rem`.
  - `getComputedStyle(document.querySelector('.monitor-gpu-metric__fill--util')).backgroundColor` is not the exact `var(--chart-2)`-driven value used by the theme, and still resolves through the older desaturated mix.
  - `getComputedStyle(document.querySelector('.monitor-gpu-metric__fill--memory')).backgroundColor` is not the exact `var(--chart-1)`-driven value used by the theme.
  - `getComputedStyle(document.querySelector('.monitor-gpu-row[data-active="true"] .monitor-gpu-row__index')).backgroundColor` is fully transparent or still neutral instead of a theme-tinted filled cue.
  - `getComputedStyle(document.querySelector('.monitor-card__footer')).paddingTop` and `paddingBottom` do not match the target compact values, and the footer gap does not match the target compact value.
- GREEN after implementation:
  - `getComputedStyle(document.querySelector('.monitor-dashboard-grid')).getPropertyValue('--monitor-dashboard-card-min').trim()` is `22rem`.
  - Util and memory fills resolve to the exact chart-token colors, not color-mix fallback/desaturation.
  - The active `G#` index has a nontransparent theme-tinted fill.
  - Footer padding and gap match the compact target values.
  - Three columns at 1440px remains a visual acceptance check only, not a RED premise.

**Remote commands**
```bash
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run check'
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run build'
```

**Commit**
```bash
git add frontend/src/routes/+page.svelte frontend/src/lib/components/ServerCard.svelte frontend/src/lib/components/GpuBar.svelte frontend/src/lib/styles/monitor-dashboard.css frontend/src/lib/styles/monitor-cards.css
git commit -m "feat: densify Full cards"
```

### Task 3: Replace the header scroll logic with the exact collapse helper

**Interfaces**
- `const HEADER_SCROLL_DIRECTION_THRESHOLD_PX = 30`
- `const HEADER_TOP_RESET_PX = 12`
- `const HEADER_OUTER_GUTTER_MIN_PX = 48`
- `const HEADER_INDICATOR_TOP_MIN_PX = 12`
- `const HEADER_INDICATOR_TOP_MAX_PX = 16`
- `type HeaderScrollDirection = 'up' | 'down' | null`
- `interface HeaderVisibilityInput { currentY: number; previousY: number; direction: HeaderScrollDirection; accumulatedDelta: number; currentCompact: boolean; reducedMotion: boolean; hasOuterGutter: boolean; viewportWidth: number; }`
- `interface HeaderVisibilityResult { compact: boolean; indicatorVisible: boolean; nextPreviousY: number; nextDirection: HeaderScrollDirection; nextAccumulatedDelta: number; }`
- `function updateHeaderVisibility(input: HeaderVisibilityInput): HeaderVisibilityResult`

**Exact behavior**
- Top `<= 12` forces expanded.
- A direction change resets the accumulation.
- Downward motion `>= 30` compacts.
- Upward motion `>= 30` expands.
- Motion smaller than `30` preserves the current compact state exactly.
- `reducedMotion` is explicit and keeps the same immediate top/direction semantics with no animation or timer dependency.
- Desktop indicator visibility is CSS-only and equals `compact && hasOuterGutter && viewportWidth >= 1200`.
- The indicator top offset is a CSS position in the `12px` to `16px` band, never a scroll comparison.
- The shell collapses by block size, translates, and fades.
- Hover and focus restore the header into flow; it never overlays cards.

**Exact helper snippet**
```ts
export function updateHeaderVisibility(input: HeaderVisibilityInput): HeaderVisibilityResult {
  const delta = input.currentY - input.previousY;
  const nextDirection: HeaderScrollDirection = delta > 0 ? 'down' : delta < 0 ? 'up' : input.direction;
  const directionChanged = input.direction !== null && nextDirection !== input.direction;
  const nextAccumulatedDelta = input.currentY <= HEADER_TOP_RESET_PX
    ? 0
    : directionChanged
      ? Math.abs(delta)
      : input.accumulatedDelta + Math.abs(delta);

  if (input.currentY <= HEADER_TOP_RESET_PX) {
    return {
      compact: false,
      indicatorVisible: false,
      nextPreviousY: input.currentY,
      nextDirection,
      nextAccumulatedDelta: 0
    };
  }

  const crossedThreshold = nextAccumulatedDelta >= HEADER_SCROLL_DIRECTION_THRESHOLD_PX;
  const compact = crossedThreshold ? nextDirection === 'down' : input.currentCompact;

  return {
    compact,
    indicatorVisible: compact && input.hasOuterGutter && input.viewportWidth >= 1200,
    nextPreviousY: input.currentY,
    nextDirection,
    nextAccumulatedDelta
  };
}
```

**Exact tests**
```ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { updateHeaderVisibility } from './headerVisibility.ts';

test('keeps a compact header compact on subthreshold downward motion', () => {
  const result = updateHeaderVisibility({
    currentY: 18,
    previousY: 0,
    direction: null,
    accumulatedDelta: 0,
    currentCompact: true,
    reducedMotion: false,
    hasOuterGutter: true,
    viewportWidth: 1440
  });

  assert.equal(result.compact, true);
});

test('keeps an expanded header expanded on subthreshold upward motion', () => {
  const result = updateHeaderVisibility({
    currentY: 24,
    previousY: 42,
    direction: 'up',
    accumulatedDelta: 18,
    currentCompact: false,
    reducedMotion: false,
    hasOuterGutter: true,
    viewportWidth: 1440
  });

  assert.equal(result.compact, false);
});
```

**RED / GREEN**
- RED: current header logic still changes compact state before the threshold or uses scrollY to decide indicator visibility.
- GREEN: the helper tests pass, the page uses the helper constants, and the header no longer overlays cards while collapsing.

**Remote commands**
```bash
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && node --experimental-strip-types --test src/lib/utils/headerVisibility.test.ts'
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run check'
```

**Commit**
```bash
git add frontend/src/lib/utils/headerVisibility.ts frontend/src/lib/utils/headerVisibility.test.ts frontend/src/routes/+page.svelte frontend/src/lib/styles/monitor-dashboard.css
git commit -m "feat: collapse the dashboard header safely"
```

### Task 4: Make Compact temporary-overlay only and remove the persistent detail rail

**Interfaces**
- `interface CompactDashboardProps { servers: ServerState[]; }`
- `interface CompactServerRowProps { server: ServerState; selected?: boolean; onSelect: (serverId: number) => void; onRegisterRow?: (serverId: number, element: HTMLElement | null) => void; onTooltipChange?: (tooltip: CompactTooltip | null) => void; }`
- `interface CompactServerDetailProps { server: ServerState | null; onClose?: () => void; titleId?: string; mode?: 'overlay' | 'sheet'; autofocusClose?: boolean; }`

**Exact markup direction**
- Remove the persistent aside, placeholder, and second grid column.
- Render the detail only when `selectedServer && isDesktop`.
- Render the sheet only when `selectedServer && !isDesktop`.
- Remove `showNetwork`, IP, and freshness lines from Compact.
- Keep the row list full-width and wrapped with no horizontal page or row scroll.

**Exact conditional snippet**
```svelte
{#if selectedServer && isDesktop}
  <div class="compact-detail-overlay">
    <CompactServerDetail
      server={selectedServer}
      mode="overlay"
      titleId="compact-detail-title-desktop"
      onClose={closeSelection}
    />
  </div>
{/if}

{#if selectedServer && !isDesktop}
  <div class="compact-sheet-backdrop">
    <div class="compact-sheet" role="dialog" aria-modal="true" aria-labelledby="compact-detail-title-mobile">
      <CompactServerDetail
        server={selectedServer}
        mode="sheet"
        titleId="compact-detail-title-mobile"
        autofocusClose={true}
        onClose={closeSelection}
      />
    </div>
  </div>
{/if}
```

**Focused CSS replacement snippet**
```css
.compact-dashboard,
.compact-dashboard__list,
.compact-row {
  min-width: 0;
  overflow-x: clip;
}

.compact-row {
  grid-template-columns: minmax(0, 1fr);
}

.compact-row__slots {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(2.6rem, 1fr));
  gap: 0.24rem;
}

.compact-detail-overlay {
  position: fixed;
  right: 1rem;
  top: 1rem;
  width: min(30rem, calc(100vw - 2rem));
}

.compact-detail__placeholder {
  display: none;
}
```

**RED / GREEN browser assertions**
- RED before implementation:
  - `.compact-dashboard__detail-panel` exists.
  - `.compact-detail__placeholder` exists.
  - `scrollWidth > clientWidth` at 1440 and 390 wide.
  - `showNetwork`, IP, or freshness text still appears in Compact.
- GREEN after implementation:
  - The persistent rail is gone.
  - The placeholder is gone.
  - `scrollWidth === clientWidth`.
  - Selected detail appears only when explicitly opened.
  - GPU rows wrap without horizontal page or row scroll.

**Remote commands**
```bash
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run check'
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run build'
```

**Commit**
```bash
git add frontend/src/lib/components/CompactDashboard.svelte frontend/src/lib/components/CompactServerRow.svelte frontend/src/lib/components/CompactServerDetail.svelte frontend/src/lib/styles/monitor-compact.css frontend/src/routes/+page.svelte
git commit -m "feat: make Compact availability-only"
```

### Task 5: Run the remote dev service, browser QA, and diff check

**Remote service and tunnel**
```bash
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && ./run_development.sh status'
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && ./run_development.sh start'
ssh -4 -N -L 15175:127.0.0.1:5175 -p 2200 ircv@166.104.167.11
```

**Local Playwright wrapper sequence**
```bash
export PWCLI=/Users/shchoi/.codex/skills/playwright/scripts/playwright_cli.sh
mkdir -p /Users/shchoi/workspace/output/dense-gpu-dashboard

bash "$PWCLI" open http://127.0.0.1:15175/
bash "$PWCLI" resize 1440 1000
bash "$PWCLI" eval '() => { document.cookie = "themeMode=dark; path=/"; document.cookie = "colorTheme=blue; path=/"; document.cookie = "dashboardView=default; path=/"; location.reload(); return true; }'
bash "$PWCLI" eval '() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth, fullColumns: new Set(Array.from(document.querySelectorAll(".monitor-dashboard-card-item")).slice(0, 6).map((node) => Math.round(node.getBoundingClientRect().left))).size, headerHeight: Math.round(document.querySelector(".ops-header-shell").getBoundingClientRect().height) })'
bash "$PWCLI" eval '() => Array.from(document.querySelectorAll(".monitor-card__title")).map((el) => el.textContent?.trim() ?? "")'
bash "$PWCLI" screenshot /Users/shchoi/workspace/output/dense-gpu-dashboard/full-dark-1440-top.png
bash "$PWCLI" mousewheel 0 960
bash "$PWCLI" eval '() => ({ headerCollapsed: document.querySelector(".ops-header-shell").classList.contains("ops-header-compact"), headerHeight: Math.round(document.querySelector(".ops-header-shell").getBoundingClientRect().height) })'
bash "$PWCLI" screenshot /Users/shchoi/workspace/output/dense-gpu-dashboard/full-dark-1440-scrolled.png

bash "$PWCLI" mousewheel 0 -960
bash "$PWCLI" eval '() => { document.cookie = "themeMode=light; path=/"; location.reload(); return true; }'
bash "$PWCLI" resize 1440 1000
bash "$PWCLI" screenshot /Users/shchoi/workspace/output/dense-gpu-dashboard/full-light-1440-top.png
bash "$PWCLI" mousewheel 0 960
bash "$PWCLI" screenshot /Users/shchoi/workspace/output/dense-gpu-dashboard/full-light-1440-scrolled.png

bash "$PWCLI" mousewheel 0 -960
bash "$PWCLI" eval '() => { document.cookie = "dashboardView=compact; path=/"; document.cookie = "themeMode=dark; path=/"; location.reload(); return true; }'
bash "$PWCLI" resize 1440 1000
bash "$PWCLI" eval '() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth, hasRail: document.querySelector(".compact-dashboard__detail-panel") !== null, hasPlaceholder: document.querySelector(".compact-detail__placeholder") !== null, compactOrder: Array.from(document.querySelectorAll(".compact-row__name")).map((el) => el.textContent?.trim() ?? "") })'
bash "$PWCLI" eval '() => document.querySelector(".compact-row__select")?.click()'
bash "$PWCLI" screenshot /Users/shchoi/workspace/output/dense-gpu-dashboard/compact-dark-1440-top.png
bash "$PWCLI" resize 390 844
bash "$PWCLI" screenshot /Users/shchoi/workspace/output/dense-gpu-dashboard/compact-dark-390x844.png

bash "$PWCLI" eval '() => { document.cookie = "themeMode=light; path=/"; location.reload(); return true; }'
bash "$PWCLI" resize 1440 1000
bash "$PWCLI" screenshot /Users/shchoi/workspace/output/dense-gpu-dashboard/compact-light-1440-top.png
bash "$PWCLI" resize 390 844
bash "$PWCLI" screenshot /Users/shchoi/workspace/output/dense-gpu-dashboard/compact-light-390x844.png
```

**Assertions to confirm before finishing**
- `scrollWidth === clientWidth` on Full and Compact.
- Three visible card columns at 1440 on Full.
- The header shell height is smaller after scroll than at top.
- `.compact-dashboard__detail-panel` is absent.
- `.compact-detail__placeholder` is absent.
- The Compact order list matches the Full order list exactly.
- Dark and light screenshots are saved in:
  - `/Users/shchoi/workspace/output/dense-gpu-dashboard/full-dark-1440-top.png`
  - `/Users/shchoi/workspace/output/dense-gpu-dashboard/full-dark-1440-scrolled.png`
  - `/Users/shchoi/workspace/output/dense-gpu-dashboard/full-light-1440-top.png`
  - `/Users/shchoi/workspace/output/dense-gpu-dashboard/full-light-1440-scrolled.png`
  - `/Users/shchoi/workspace/output/dense-gpu-dashboard/compact-dark-1440-top.png`
  - `/Users/shchoi/workspace/output/dense-gpu-dashboard/compact-dark-390x844.png`
  - `/Users/shchoi/workspace/output/dense-gpu-dashboard/compact-light-1440-top.png`
  - `/Users/shchoi/workspace/output/dense-gpu-dashboard/compact-light-390x844.png`

**Final diff check**
```bash
ssh -4 -p 2200 ircv@166.104.167.11 'cd ~/workspace/monitoring_v2_dev && git diff --check -- docs/superpowers/plans/2026-07-14-dense-apple-gpu-dashboard-implementation.md'
```

**Stop rule**
- Do not push.
- Do not touch any file other than this plan file during the rewrite pass.
- Finish only after the diff check is clean.

## Self-review checklist
- [ ] The plan is file-bounded and concise.
- [ ] The active theme tokens are exact.
- [ ] `Default` is removed from the page menu path.
- [ ] Full card work targets 22rem cards, three columns at 1440, `--chart-2` util, `--chart-1` memory, and active `G#` fill.
- [ ] Header logic uses the new helper and preserves sub-threshold state.
- [ ] Compact uses overlay or sheet only, with no persistent rail or placeholder.
- [ ] Browser QA uses the local wrapper and exact artifact paths.
- [ ] `git diff --check` is included.
