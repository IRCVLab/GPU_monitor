// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const dashboardSource = readFileSync(new URL('./CompactDashboard.svelte', import.meta.url), 'utf8');
const rowSource = readFileSync(new URL('./CompactServerRow.svelte', import.meta.url), 'utf8');
const cssSource = readFileSync(new URL('../styles/monitor-compact.css', import.meta.url), 'utf8');

function cssRule(source, selector) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = source.match(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`, 'm'));
	assert.ok(match?.groups?.body, `Missing CSS rule for ${selector}`);
	return match.groups.body.replace(/\s+/g, ' ').trim();
}

function assertDeclaration(rule, property, valuePattern) {
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	assert.match(rule, new RegExp(`${escapedProperty}\\s*:\\s*${valuePattern}\\s*;`));
}

test('compact dashboard removes the inspector flow and renders a fixed banked matrix contract', () => {
	assert.doesNotMatch(dashboardSource, /CompactServerDetail|compact-dashboard__inspector|compact-sheet/);
	assert.match(dashboardSource, /compactGpuBankCount/);
	assert.match(dashboardSource, /compactGpuBankSlots/);
	assert.match(dashboardSource, /compact-dashboard__column-header/);
});

test('compact bank selector uses ordinary button pressed and current semantics', () => {
	assert.doesNotMatch(dashboardSource, /role="tablist"|role=\{'tablist'\}/);
	assert.doesNotMatch(dashboardSource, /role="tab"|role=\{'tab'\}/);
	assert.match(dashboardSource, /aria-pressed=\{bankIndex === activeBankIndex\}/);
	assert.match(dashboardSource, /aria-current=\{bankIndex === activeBankIndex \? 'true' : undefined\}/);
});

test('compact dashboard fetches active hold cues locally when parent data is absent', () => {
	assert.match(dashboardSource, /import \{ getNotes \} from '\$lib\/api';/);
	assert.match(dashboardSource, /type CompactHoldCue = \{/);
	assert.match(dashboardSource, /function parseNoteTime\(iso: string \| null\): number \| null/);
	assert.match(dashboardSource, /function noteVisible\(note: Note, nowMs: number\): boolean/);
	assert.match(dashboardSource, /note\.kind !== 'hold'/);
	assert.match(dashboardSource, /serverIds\.map\(async \(serverId\) => \{/);
	assert.match(dashboardSource, /getNotes\(serverId\)/);
	assert.match(dashboardSource, /const resolvedHeldGpuIndicesByServer = \$derived\.by\(/);
	assert.match(dashboardSource, /heldGpuIndicesByServer\?\.get\(serverId\) \?\? fetchedHeldGpuIndicesByServer\.get\(serverId\)/);
	assert.match(dashboardSource, /holdCuesByGpu=\{fetchedHoldCuesByServer\.get\(server\.server_id\)\}/);
	assert.match(dashboardSource, /heldGpuIndices=\{resolvedHeldGpuIndicesByServer\.get\(server\.server_id\)\}/);
});

test('compact rows support absent placeholders, keep held overlays orthogonal to telemetry state, and hide visible status copy', () => {
	assert.match(rowSource, /data-state=\{state\}/);
	assert.match(rowSource, /data-state="absent"|data-state=\{'absent'\}/);
	assert.match(rowSource, /data-held=\{heldGpuIndices\?\.has\(gpu\.index\) \? 'true' : undefined\}/);
	assert.doesNotMatch(rowSource, />\{statusConfig\[server\.status\].*label/);
	assert.match(cssSource, /\.compact-slot\[data-held='true'\]::after/);
});

test('compact occupied cells show full usernames inline and allow wrapping growth inside one server row', () => {
	assert.doesNotMatch(rowSource, /getLinuxUsernameInitials/);
	assert.doesNotMatch(rowSource, /compact-slot__badge/);
	assert.match(rowSource, /\{#each users as user, index/);
	assert.match(rowSource, /class="compact-slot__user-list"/);
	assert.match(rowSource, /class="compact-slot__username"/);

	const slotRule = cssRule(cssSource, '.compact-slot');
	assertDeclaration(slotRule, 'min-height', '1\.72rem');
	assert.doesNotMatch(slotRule, /(?:^|[ ;])height:\s*1\.72rem/);
	const usersRule = cssRule(cssSource, '.compact-slot__users');
	assertDeclaration(usersRule, 'height', 'auto');
	assertDeclaration(usersRule, 'align-items', 'flex-start');
	const userListRule = cssRule(cssSource, '.compact-slot__user-list');
	assertDeclaration(userListRule, 'display', 'grid');
	assertDeclaration(userListRule, 'white-space', 'normal');
	assertDeclaration(userListRule, 'word-break', 'normal');
	const usernameRule = cssRule(cssSource, '.compact-slot__username');
	assertDeclaration(usernameRule, 'font-size', '0\.58rem');
	assertDeclaration(usernameRule, 'line-height', '1\.1');
	assertDeclaration(usernameRule, 'white-space', 'nowrap');
	assertDeclaration(usernameRule, 'overflow', 'hidden');
	assertDeclaration(usernameRule, 'text-overflow', 'ellipsis');
});

test('compact rack css keeps eight fixed gpu columns and passive absent slots', () => {
	assert.doesNotMatch(cssSource, /repeat\(auto-fit/);
	assert.match(cssSource, /grid-template-columns:\s*clamp\(4\.5rem,\s*18vw,\s*8\.25rem\)\s*repeat\(8,\s*minmax\(22px,\s*1fr\)\)/);
	assert.doesNotMatch(cssSource, /grid-template-columns:\s*minmax\(0,\s*1fr\)/);

	const availableRule = cssRule(cssSource, ".compact-slot[data-state='available']");
	assert.match(availableRule, /var\(--ops-primary\)/);
	assert.ok(/border[^;]*var\(--ops-primary\)|outline[^;]*var\(--ops-primary\)|box-shadow:[^;]*var\(--ops-primary\)/.test(availableRule), 'available slots must use a var(--ops-primary) outline');

	const occupiedRule = cssRule(cssSource, ".compact-slot[data-state='occupied']");
	assert.match(occupiedRule, /background:\s*var\(--ops-primary\);/);
	assert.match(occupiedRule, /color:\s*var\(--ops-on-primary\);/);

	const usernameRule = cssRule(cssSource, '.compact-slot__username');
	assertDeclaration(usernameRule, 'color', 'inherit');

	const unknownRule = cssRule(cssSource, ".compact-slot[data-state='unknown']");
	assert.doesNotMatch(unknownRule, /var\(--ops-primary\)|#22c55e|var\(--chart-2\)/);

	const absentRule = cssRule(cssSource, ".compact-slot[data-state='absent']");
	assertDeclaration(absentRule, 'pointer-events', 'none');
	assert.match(absentRule, /background:\s*transparent/);
});

test('compact mobile css keeps one row, disables per-cell touch, and preserves the row touch target', () => {
	assert.match(cssSource, /\.compact-row__select\s*\{[^}]*position:\s*absolute[^}]*inset:\s*0[^}]*cursor:\s*pointer/s);
	assert.match(cssSource, /@media \(max-width: 767px\) \{[\s\S]*\.compact-slot__users\s*\{[^}]*pointer-events:\s*none/s);
	assert.doesNotMatch(cssSource, /@media \(max-width: 767px\) \{[\s\S]*\.compact-row\s*\{[^}]*grid-template-columns\s*:/s);
	assert.doesNotMatch(cssSource, /@media \(max-width: 767px\) \{[\s\S]*\.compact-row\s*\{[^}]*display:\s*block/s);
	assert.doesNotMatch(cssSource, /@media \(max-width: 767px\) \{[\s\S]*\.compact-row\s*\{[^}]*display:\s*flex/s);
});

test('compact server identity leaves the full-row button hit target unobstructed', () => {
	const identityRule = cssRule(cssSource, '.compact-row__identity');
	assertDeclaration(identityRule, 'pointer-events', 'none');
});

test('compact hold refresh preserves prior cues and exposes failures instead of masking them as empty', () => {
	assert.match(dashboardSource, /type CompactHoldLoadResult = \{/);
	assert.match(dashboardSource, /failedServerIds: Set<number>/);
	assert.match(dashboardSource, /previousNotes\.get\(serverId\) \?\? \[\]/);
	assert.match(dashboardSource, /compactHoldLoadErrors = result\.failedServerIds/);
	assert.doesNotMatch(dashboardSource, /catch \{\s*return \[server\.server_id, \[\] as Note\[\]\]/);
	assert.match(dashboardSource, /HOLD 정보 확인 지연/);
});

test('compact row activation always opens full while gpu hover and focus only reveal a passive hint', () => {
	assert.doesNotMatch(rowSource, /if \(occupiedSlots\.length > 0\) \{/);
	assert.doesNotMatch(rowSource, /openTooltip\(event\.currentTarget, popoverItems\(occupiedSlots\)\)/);
	assert.match(rowSource, /class="compact-row__select"[\s\S]*onclick=\{openFull\}/);
	assert.match(rowSource, /onmouseenter=\{\(event\) => openTooltip\(event\.currentTarget, popoverItem\(gpu, users\)\)\}/);
	assert.match(rowSource, /onfocus=\{\(event\) => openTooltip\(event\.currentTarget, popoverItem\(gpu, users\)\)\}/);
	assert.match(rowSource, /onclick=\{openFull\}/);
	assert.doesNotMatch(dashboardSource, />Full에서 보기</);
	assert.doesNotMatch(dashboardSource, /compact-dashboard__tooltip-action|compact-dashboard__tooltip-footer|tooltipActionButton|focusAction/);
	assert.match(dashboardSource, /role="tooltip"/);
	const tooltipRule = cssRule(cssSource, '.compact-dashboard__tooltip');
	assertDeclaration(tooltipRule, 'pointer-events', 'none');
});

test('compact gpu hint carries exact gpu, telemetry truth, owners, and hold annotation without interactive affordances', () => {
	assert.match(rowSource, /stateLabel:/);
	assert.match(rowSource, /ownersLabel:/);
	assert.match(rowSource, /holdLabel:/);
	assert.match(rowSource, /heldGpuIndices\?\.has\(gpu\.index\) \? 'HOLD' : ''/);
	assert.match(dashboardSource, /compact-dashboard__tooltip-gpu/);
	assert.match(dashboardSource, /compact-dashboard__tooltip-state/);
	assert.match(dashboardSource, /compact-dashboard__tooltip-users/);
	assert.match(dashboardSource, /compact-dashboard__tooltip-hold/);
	assert.doesNotMatch(dashboardSource, /compact-dashboard__tooltip[\s\S]*<button/);

	const entryRule = cssRule(cssSource, '.compact-dashboard__tooltip-entry');
	assert.ok(entryRule.includes('grid-template-columns: auto minmax(0, 1fr)'));
	assertDeclaration(entryRule, 'align-items', 'baseline');
	const metaRule = cssRule(cssSource, '.compact-dashboard__tooltip-meta');
	assertDeclaration(metaRule, 'display', 'flex');
	assertDeclaration(metaRule, 'gap', '0\.35rem');
	const holdRule = cssRule(cssSource, '.compact-dashboard__tooltip-hold');
	assertDeclaration(holdRule, 'font-weight', '700');
});

test('compact held overlay uses a full-style collar and notch on the exact cell without recoloring telemetry state', () => {
	const heldRule = cssRule(cssSource, ".compact-slot[data-held='true']");
	assert.match(heldRule, /box-shadow:\s*inset 0 0 0 1px/);
	assert.doesNotMatch(heldRule, /background:|border-color:/);

	const notchRule = cssRule(cssSource, ".compact-slot[data-held='true']::after");
	assert.match(notchRule, /content:\s*''/);
	assert.match(notchRule, /position:\s*absolute/);
	assert.match(notchRule, /height:\s*2px/);
	assert.match(notchRule, /background:\s*#f59e0b/);
});

test('compact dashboard preserves incoming server order by rendering rows directly from servers', () => {
	assert.match(dashboardSource, /\{#each servers as server \(server\.server_id\)\}/);
	assert.doesNotMatch(dashboardSource, /\.sort\(|orderServers\(|reversed\(|slice\(\)\.reverse\(/);
});


test('compact hold polling depends on stable server ids instead of high-frequency telemetry objects', () => {
	assert.match(dashboardSource, /const serverIdSignature = \$derived\(/);
	assert.match(dashboardSource, /async function loadHoldNotes\([\s\S]*serverIds: readonly number\[\]/);
	assert.match(dashboardSource, /const serverIdSnapshot = parseServerIdSignature\(serverIdSignature\);/);
	assert.doesNotMatch(dashboardSource, /const serverSnapshot = \[\.\.\.servers\]/);
});

test('compact hold refresh ignores stale async responses after the server snapshot changes', () => {
	assert.match(
		dashboardSource,
		/async function loadHoldNotes\([\s\S]*serverIds: readonly number\[\],[\s\S]*previousNotes: ReadonlyMap<number, Note\[]>[\s\S]*\): Promise<CompactHoldLoadResult>/
	);
	assert.match(
		dashboardSource,
		/const result = await loadHoldNotes\(serverIdSnapshot, holdNotesCache\);[\s\S]*if \(cancelled\) return;[\s\S]*compactHoldNotesByServer = result\.notesByServer;/
	);
	assert.doesNotMatch(
		dashboardSource,
		/async function loadHoldNotes[\s\S]*compactHoldNotesByServer = new Map\(entries\)/
	);
});

test("compact hold polling serializes slow refresh batches for the same server snapshot", () => {
	assert.match(dashboardSource, /let holdRefreshInFlight = false;/);
	assert.match(dashboardSource, /if \(holdRefreshInFlight\) return;[\s\S]*holdRefreshInFlight = true;/);
	assert.match(dashboardSource, /finally \{[\s\S]*holdRefreshInFlight = false;[\s\S]*\}/);
});


test('compact hold cues use concise active relative units without 남음', () => {
	assert.match(dashboardSource, /if \(remainingMs <= 0\) return '만료됨';/);
	assert.ok(dashboardSource.includes('return `${seconds}초`;'));
	assert.ok(dashboardSource.includes('return `${minutes}분`;'));
	assert.ok(dashboardSource.includes('return `${hours}시간`;'));
	assert.ok(dashboardSource.includes('return `${Math.ceil(hours / 24)}일`;'));
	assert.doesNotMatch(dashboardSource, /`\$\{[^}]+\}(?:초|분|시간|일) 남음`/);
});

test('compact row sorts owners for display and tooltip text, keys a height-stable slot identity, and flies motion with reduced-motion fallback', () => {
	assert.match(rowSource, /function displayUsers\(gpu: GpuInfo\): string\[\] \{[\s\S]*\[\.\.\.gpu\.users\]\.sort\(\)/);
	assert.ok(rowSource.includes("return users.join('\\u0000') || 'idle';"));
	assert.match(rowSource, /\{@const users = displayUsers\(gpu\)\}/);
	assert.match(rowSource, /import \{ prefersReducedMotion \} from 'svelte\/motion';/);
	assert.match(rowSource, /import \{ cubicOut \} from 'svelte\/easing';/);
	assert.match(rowSource, /import \{ fly \} from 'svelte\/transition';/);
	assert.match(rowSource, /const slotIdentityInFly = \$derived\(\{[\s\S]*y: prefersReducedMotion\.current \? 0 : 2,[\s\S]*opacity: prefersReducedMotion\.current \? 1 : 0,[\s\S]*duration: prefersReducedMotion\.current \? 0 : 220,[\s\S]*easing: cubicOut[\s\S]*\}\);/);
	assert.match(rowSource, /const slotIdentityOutFly = \$derived\(\{[\s\S]*y: prefersReducedMotion\.current \? 0 : -2,[\s\S]*opacity: prefersReducedMotion\.current \? 1 : 0,[\s\S]*duration: prefersReducedMotion\.current \? 0 : 160,[\s\S]*easing: cubicOut[\s\S]*\}\);/);
	assert.match(rowSource, /ownersLabel:\s*users\.length > 0 \? users\.join\(', '\) : 'idle'/);
	assert.match(rowSource, /class="compact-slot__identity-slot"/);
	assert.match(rowSource, /\{#key `\$\{gpu\.index\}:\$\{state\}:\$\{displayUsersSignature\(users\)\}`\}[\s\S]*class="compact-slot__identity-set"[\s\S]*in:fly=\{slotIdentityInFly\}[\s\S]*out:fly=\{slotIdentityOutFly\}/);
	assert.doesNotMatch(rowSource, /\{#each gpu\.users as user, index/);
	assert.match(rowSource, /\{#each users as user, index \(`/);
});

test('compact slot surface transitions settle in 240ms and reduced motion disables them', () => {
	const slotRule = cssRule(cssSource, '.compact-slot');
	assert.match(slotRule, /transition:\s*border-color 240ms cubic-bezier\(0\.22, 1, 0\.36, 1\), background-color 240ms cubic-bezier\(0\.22, 1, 0\.36, 1\), color 240ms cubic-bezier\(0\.22, 1, 0\.36, 1\), box-shadow 240ms cubic-bezier\(0\.22, 1, 0\.36, 1\);/);
	const reducedStart = cssSource.indexOf('@media (prefers-reduced-motion: reduce)');
	assert.notEqual(reducedStart, -1, 'missing compact reduced-motion media query');
	const reduced = cssSource.slice(reducedStart);
	assert.match(reduced, /\.compact-slot\s*\{[^}]*transition:\s*none;/s);
	assert.doesNotMatch(reduced, /transition-duration:\s*1ms;/);
});

test('task 4 compact hold cues reuse shared noteAdvisory ranking and display fallback helpers', () => {
	assert.match(rowSource, /import \{ buildHoldAdvisory, getNotePriorityMeta, resolveDisplayName \} from '\$lib\/utils\/noteAdvisory';/);
	assert.match(rowSource, /function holdNotes\(gpu: GpuInfo\): Note\[] \{/);
	assert.match(rowSource, /buildHoldAdvisory\(holdNotes\(gpu\)\)/);
	assert.match(rowSource, /const primaryPriorityMeta = getNotePriorityMeta\(primaryHold\.priority\)/);
	assert.match(rowSource, /parts\.push\(resolveDisplayName\(primaryHold\)\)/);
	assert.match(rowSource, /holdAdvisory\.secondarySummary/);
	assert.match(rowSource, /displayName: resolveDisplayName\(note\)/);
	assert.doesNotMatch(rowSource, /owner:\s*note\.username/);
	assert.doesNotMatch(rowSource, /const parts = \[`HOLD \$\{primaryHold\.owner\}`\]/);
});

test('task 4 compact tooltip stays passive, keyboard reachable, and availability-neutral while mirroring full hold detail fields', () => {
	assert.match(rowSource, /type CompactTooltipHoldEntry = \{[\s\S]*displayName: string;[\s\S]*priorityLabel: string;[\s\S]*priorityClassName: string;[\s\S]*remaining: string;[\s\S]*memo: string;[\s\S]*\}/);
	assert.match(rowSource, /holdEntries:\s*CompactTooltipHoldEntry\[];/);
	assert.match(rowSource, /function orderedHoldEntries\(gpu: GpuInfo\): CompactHoldCue\[] \{/);
	assert.match(rowSource, /getNotePriorityMeta\(note\.priority\)/);
	assert.match(rowSource, /onmouseenter=\{\(event\) => openTooltip\(event\.currentTarget, popoverItem\(gpu, users\)\)\}/);
	assert.match(rowSource, /onfocus=\{\(event\) => openTooltip\(event\.currentTarget, popoverItem\(gpu, users\)\)\}/);
	assert.match(rowSource, /aria-describedby=\{tooltipVisible\(gpu\) \? tooltipId\(gpu\) : undefined\}/);
	assert.match(dashboardSource, /id=\{activeTooltip\.item\.tooltipId\}/);
	assert.match(dashboardSource, /role="tooltip"/);
	assert.match(dashboardSource, /compact-dashboard__tooltip-note-owner/);
	assert.match(dashboardSource, /compact-dashboard__tooltip-note-priority/);
	assert.match(dashboardSource, /compact-dashboard__tooltip-note-expiry/);
	assert.match(dashboardSource, /compact-dashboard__tooltip-note-memo/);
	assert.doesNotMatch(dashboardSource, /compact-dashboard__tooltip[\s\S]*<button/);
	assert.match(rowSource, /function gpuState\(gpu: GpuInfo\): CompactGpuState \{[\s\S]*return getCompactGpuState\(server\.status, server\.last_seen, gpu\);[\s\S]*\}/);
	assert.match(rowSource, /const availableCount = \$derived\.by\([\s\S]*gpuState\(gpu\) === 'available'[\s\S]*\);/);
	assert.doesNotMatch(rowSource, /getCompactGpuState\([^\n]*hold/);
});

test('compact dashboard closes hold tooltips on hold refresh or expiry tick so stale advisory snapshots cannot persist', () => {
	assert.match(dashboardSource, /const TOOLTIP_HEIGHT_ESTIMATE = 176;/);
	assert.match(dashboardSource, /let holdTooltipVersion = \$state\(0\);/);
	assert.match(dashboardSource, /let activeTooltipVersion = \$state<number \| null>\(null\);/);
	assert.match(dashboardSource, /function tooltipHasHoldAdvisory\(tooltip: CompactPopover\): boolean \{/);
	assert.match(dashboardSource, /activeTooltipVersion = holdTooltipVersion;/);
	assert.match(dashboardSource, /nowMs = Date\.now\(\);[\s\S]*holdTooltipVersion \+= 1;/);
	assert.match(dashboardSource, /compactHoldNotesByServer = result\.notesByServer;[\s\S]*compactHoldLoadErrors = result\.failedServerIds;[\s\S]*holdTooltipVersion \+= 1;/);
	assert.match(dashboardSource, /\$effect\(\(\) => \{[\s\S]*if \(!activeTooltip \|\| activeTooltipVersion === null \|\| !tooltipHasHoldAdvisory\(activeTooltip\)\) return;[\s\S]*if \(activeTooltipVersion === holdTooltipVersion\) return;[\s\S]*closeTooltip\(\);[\s\S]*\}\);/);
});

test('compact ordered hold entries preserve note object identity instead of joining by note ids', () => {
	assert.match(rowSource, /const cueByNote = new Map\(cues\.map\(\(entry\) => \[entry\.note, entry\] as const\)\);/);
	assert.match(rowSource, /return holdAdvisory\.ordered[\s\S]*cueByNote\.get\(note\)/);
	assert.doesNotMatch(rowSource, /entry\.note\.id === note\.id/);
});
