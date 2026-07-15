# Quiet Rack Task 5 Report

## Scope
Refine the Full card header and system density so the card reads as one quiet instrument: identity first, GPU rows second, collapsed system as fixed micro-summary, and expanded system dense rather than boxy.

## Files Changed
- `frontend/src/lib/components/ServerCard.svelte`
- `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- `frontend/src/lib/styles/monitor-cards.css`
- `frontend/src/lib/styles/monitor-cards.contract.test.ts`

## RED Evidence
Command:

```bash
cd frontend
node --experimental-strip-types --test src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts
```

Result: exit 1.

Observed failures:
- `ServerCard hides the normal status label while keeping exception labels visible`
- `ServerCard renders collapsed system preview as four named micro-items`
- `task 5 full card header and system density follow the quiet instrument contract`

## GREEN Evidence
### Targeted Task 5 contracts
Command:

```bash
cd frontend
node --experimental-strip-types --test src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts
```

Result: exit 0, 23/23 passing.

### Full frontend Node test suite
Command:

```bash
cd frontend
node --experimental-strip-types --test $(find src -name "*.test.ts" -print | sort)
```

Result: exit 0, 132/132 passing.

### Svelte check
Command:

```bash
cd frontend
npm run check
```

Result: exit 0, `svelte-check found 0 errors and 0 warnings`.

### Production build
Command:

```bash
cd frontend
npm run build
```

Result: exit 0.

Note: Vite logged standard `node:async_hooks` browser-externalization notices from Svelte/SvelteKit internals during build; the build completed successfully.

## Behavior Summary
- Online `정상` status text is now visually hidden while keeping the health dot and preserving visible exception labels for degraded/offline/unknown states.
- Host/IP plus refresh remain a single restrained secondary line with truncation protection instead of wrapping.
- The collapsed system preview now renders fixed `CPU` / `RAM` / `GPU` / `Disk` micro-items with tabular values and threshold-only warning emphasis.
- The expanded system now opens with a compact summary grid and denser transparent hardware/mount rows instead of nested card-like boxes.
- Footer rhythm stays compact and aligned with dense GPU rows without changing drag, note/hold, ordering, or API behavior.

## Task 5 Review Follow-up

### Scope
Fix verified review issues in the collapsed system summary only: preserve the child-driven accessible name, switch collapsed RAM to percentage, and make the four preview micro-columns shrink-safe at mobile widths without changing expanded RAM detail formatting.

### Files Changed
- `frontend/src/lib/components/ServerCard.svelte`
- `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- `frontend/src/lib/styles/monitor-cards.css`
- `frontend/src/lib/styles/monitor-cards.contract.test.ts`

### RED Evidence
Command:

```bash
source ~/.nvm/nvm.sh
cd /home/ircv/workspace/monitoring_v2_dev/frontend
node --experimental-strip-types --test src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts
```

Result: exit 1.

Observed failures:
- `ServerCard renders collapsed system preview as four named micro-items`
- `task 5 full card header and system density follow the quiet instrument contract`

### GREEN Evidence
#### Targeted Task 5 follow-up contracts
Command:

```bash
source ~/.nvm/nvm.sh
cd /home/ircv/workspace/monitoring_v2_dev/frontend
node --experimental-strip-types --test src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts
```

Result: exit 0, 23/23 passing.

#### Full frontend Node test suite
Command:

```bash
source ~/.nvm/nvm.sh
cd /home/ircv/workspace/monitoring_v2_dev/frontend
find ./src -name "*.test.ts" -print | sort | xargs node --experimental-strip-types --test
```

Result: exit 0, 132/132 passing.

#### Svelte check
Command:

```bash
source ~/.nvm/nvm.sh
cd /home/ircv/workspace/monitoring_v2_dev/frontend
npm run check
```

Result: exit 0, `svelte-check found 0 errors and 0 warnings`.

#### Production build
Command:

```bash
source ~/.nvm/nvm.sh
cd /home/ircv/workspace/monitoring_v2_dev/frontend
npm run build
```

Result: exit 0.

Note: Vite logged standard `node:async_hooks` browser-externalization notices from Svelte/SvelteKit internals during build; the build completed successfully.

#### Browser verification
- 360px viewport via Playwright: `innerWidth=360`, `docScrollWidth=360`, `bodyScrollWidth=360`, `overflowCards=[]`, first collapsed system button text `시스템 CPU 1% RAM 6% GPU 115W Disk 62%`, and every `.monitor-card__system-preview` had `aria-label=null`.
- 390px viewport via Playwright device emulation: `innerWidth=390`, `docScrollWidth=390`, `bodyScrollWidth=390`, `overflowCards=[]`, first collapsed system button text `시스템 CPU 1% RAM 6% GPU 117W Disk 62%`.
- Local evidence artifacts: `output/playwright/task5-mobile-360.png`, `output/playwright/task5-mobile-390-device.png`.

### Behavior Summary
- The collapsed system preview no longer overrides its child text with a generic `시스템 요약` label, so the button’s accessible name now naturally includes CPU/RAM/GPU/Disk values.
- Collapsed RAM now shows percentage (`ramPct.toFixed(0)%`) while expanded system detail still keeps full used/total GB formatting.
- The collapsed preview now uses four shrink-safe equal columns with `minmax(0, 1fr)`, `min-width: 0`, tabular numeric values, and value `title` text so mobile widths can compress without internal horizontal overflow.

## Final 360px footer flex correction
- Replaced auto-basis allocation with flex: 1 1 0 on footer side and preview.
- Removed the system preview width: 100%; it now consumes only remaining inline space.
- Kept four equal minmax(0, 1fr) metric columns and a fixed disclosure glyph.
- Playwright/WebKit at 360px measured every sampled footer side with scrollWidth equal to clientWidth, no overflowing cards, and document width 360.
- Targeted tests: 23/23 passed. Full Node tests, npm run check, and npm run build passed.
