# Quiet Rack Task 3 Report

- Task: Task 3 — contextual View controls and Compact→Full continuity focus
- Branch: `feature/compact-gpu-dashboard`
- Commit: `fa5c9a2` (`feat: align dashboard view controls`)

## RED evidence

Command:

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend
node --experimental-strip-types --test src/routes/page-view.contract.test.ts
```

Observed failure before implementation:

- `✖ task 3 keeps view layout controls contextual and wires compact-to-full continuity focus`
- `AssertionError [ERR_ASSERTION]: The input did not match the regular expression /\{#if \$dashboardView === 'default'\}[\s\S]*카드 배치/`

This showed the Grid/Masonry menu group was still unconditional and the Compact→Full focus handoff had not been wired yet.

## GREEN evidence

Targeted contracts:

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend
node --experimental-strip-types --test \
  src/routes/page-view.contract.test.ts \
  src/lib/components/compact-dashboard-task4.contract.test.ts
```

Result:

- `tests 25`
- `pass 25`
- `fail 0`

Static verification:

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend
npm run check
```

Result:

- `svelte-check found 0 errors and 0 warnings`

## Files

- `frontend/src/routes/page-view.contract.test.ts`
- `frontend/src/routes/+page.svelte`
- `frontend/src/lib/components/CompactDashboard.svelte`
- `frontend/src/lib/components/CompactServerRow.svelte`

## Behavior delivered

- Grid/Masonry controls and their divider render only in Full/default view.
- Full and Compact both continue to consume the existing ordered `currentServers` array without introducing sorting in Task 3 paths.
- Compact row activation now switches to Full and carries continuity focus to the same server card after render.
- Continuity focus styling is brief and self-clearing.
- Row/button keyboard activation semantics remain intact.

## Task 3 correction — compact disclosure flow preservation

### Reason for correction

A later Compact interaction change regressed the approved disclosure model by routing the row primary action straight to Full. The approved 2026-07-15 quiet-rack spec requires occupied identities to disclose in a bounded micro-popover first, with `Full에서 보기` as an explicit secondary action.

### Behavior correction

- Occupied row primary activation now opens the bounded Compact popover instead of jumping directly to Full.
- The popover now includes an explicit `Full에서 보기` button that closes the popover and then calls the existing Full continuity handoff.
- Fully non-occupied rows still go directly to Full. Rationale: when there are no occupied identities, there is no disclosure content to reveal, so preserving a direct row→Full path is the smallest predictable behavior allowed by the spec.
- Mobile keeps the row as the main touch target because occupied cell pointer interactions remain disabled under the mobile breakpoint.
- Tooltip viewport clamping and bounded mobile fit are preserved; the list area scrolls while the explicit Full action remains visible.

### Correction RED evidence

Command:

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend
node --experimental-strip-types --test src/lib/components/compact-dashboard-task4.contract.test.ts
```

Observed failure before the fix:

- `✖ compact row primary activation preserves disclosure for occupied rows and exposes explicit full action in the popover`
- `AssertionError [ERR_ASSERTION]: The input did not match the regular expression /onclick=\{\(event\) => handleRowActivation\(event\)\}/`

This confirmed the row primary action was still wired directly to Full instead of preserving the popover disclosure path.

### Correction GREEN evidence

Targeted contracts:

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend
node --experimental-strip-types --test   src/lib/components/compact-dashboard-task4.contract.test.ts   src/routes/page-view.contract.test.ts
```

Result:

- `tests 26`
- `pass 26`
- `fail 0`

All frontend Node tests:

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend
find src -type f \( -name "*.test.ts" -o -name "*.contract.test.ts" \) -print0 | sort -z | xargs -0 node --experimental-strip-types --test
```

Result:

- `tests 125`
- `pass 125`
- `fail 0`

Static verification:

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend
npm run check
```

Result:

- `svelte-check found 0 errors and 0 warnings`

Production build:

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend
npm run build
```

Result:

- `✓ built in 6.93s`
- `Using @sveltejs/adapter-node ✔ done`

### Corrected files

- `frontend/src/lib/components/CompactDashboard.svelte`
- `frontend/src/lib/components/CompactServerRow.svelte`
- `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`
- `frontend/src/routes/page-view.contract.test.ts`
- `frontend/src/lib/styles/monitor-compact.css`
