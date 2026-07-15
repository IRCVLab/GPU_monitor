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
