// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./GpuBar.svelte', import.meta.url), 'utf8');
const serverCardSource = readFileSync(new URL('./ServerCard.svelte', import.meta.url), 'utf8');
const cardCss = readFileSync(new URL('../styles/monitor-cards.css', import.meta.url), 'utf8');

function cssRule(selector) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = cardCss.match(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`, 'm'));
	assert.ok(match?.groups?.body, `Missing CSS rule for ${selector}`);
	return match.groups.body;
}

function assertDeclaration(rule, property, value) {
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const escapedValue = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	assert.match(rule, new RegExp(`${escapedProperty}\\s*:\\s*${escapedValue}\\s*;`));
}

function normalized() {
	return source.replace(/\s+/g, ' ');
}

test('GPU memory metric retains the compact GB value', () => {
	assert.match(source, /\{memUsedGB\}\/\{memTotalGB\}GB/);
});

test('GpuBar metric fills use one selected accent without chart-1 chart-2 split', () => {
	const utilRule = cssRule('.monitor-gpu-metric__fill--util');
	assertDeclaration(utilRule, 'background', 'var(--ops-primary)');

	const memoryRule = cssRule('.monitor-gpu-metric__fill--memory');
	assert.match(memoryRule, /var\(--ops-primary\)/);
	assert.doesNotMatch(memoryRule, /var\(--chart-1\)|var\(--chart-2\)/);
});

test('GpuBar accepts the shared availability state without letting holds alter it', () => {
	assert.match(source, /advisoryHolds\s*=\s*\[\]/, 'GpuBar should accept default-empty advisory hold cues');
	assert.match(source, /state:\s*availabilityState/);
	assert.match(source, /data-state=\{availabilityState\}/);
	assert.doesNotMatch(source, /data-active=/);
	assert.match(serverCardSource, /getCompactGpuState\(server\.status, server\.last_seen, gpu\)/);
	assert.match(serverCardSource, /<GpuBar[\s\S]*state=\{operationalState === 'impaired' \? staleAvailabilityState : getCompactGpuState\(server\.status, server\.last_seen, gpu\)\}/);
	assert.match(serverCardSource, /const staleAvailabilityState = \$derived\('unknown'\)/, 'impaired server cards should preserve unknown availability');
	assert.doesNotMatch(source, /state\s*=.*(?:advisoryHolds|hold)/, 'advisory holds must not alter availability semantics');
});

test('GpuBar ranks advisory holds through shared noteAdvisory helpers and keeps a dedicated held index marker', () => {
	assert.match(source, /import\s+\{\s*buildHoldAdvisory,\s*getNotePriorityMeta,\s*resolveDisplayName\s*\}\s+from\s+'\$lib\/utils\/noteAdvisory'/);
	assert.match(source, /const\s+holdAdvisory\s*=\s*\$derived\(buildHoldAdvisory\(advisoryHolds\.map\(\(\{\s*note\s*\}\)\s*=>\s*note\)\)\)/);
	assert.match(source, /const\s+primaryHold\s*=\s*\$derived\(holdAdvisory\.primary\)/);
	assert.match(source, /const\s+primaryPriorityMeta\s*=\s*\$derived\(primaryHold\s*\?\s*getNotePriorityMeta\(primaryHold\.priority\)\s*:\s*null\)/);
	assert.match(source, /const\s+primaryHoldDisplayName\s*=\s*\$derived\(primaryHold\s*\?\s*resolveDisplayName\(primaryHold\)\s*:\s*''\)/);
	assert.match(source, /monitor-gpu-row__hold-cue/, 'missing dense hold cue element');
	assert.match(source, /\{primaryHoldDisplayName\}/, 'hold cue should use display_name fallback via resolveDisplayName');
	assert.match(
		source,
		/class="monitor-gpu-row__index" data-has-hold=\{primaryHold \? 'true' : 'false'\}/,
		'the exact G# chip should advertise when it carries a HOLD collar'
	);
	assert.match(
		source,
		/\{#if primaryHold\.priority !== 'normal'\}[\s\S]*\{primaryPriorityMeta\??\.label\}[\s\S]*\{\/if\}/,
		'high and urgent cues should surface visible priority text'
	);
	assert.match(
		source,
		/\{holdAdvisory\.secondarySummary\}/,
		'multiple holds should collapse to a concise count'
	);
});

test('GpuBar cue prefixes the owner with a visible HOLD label without changing telemetry truth', () => {
	assert.match(source, /<span class="monitor-gpu-row__hold-kind">HOLD<\/span>/, 'full GPU cue should show a literal HOLD kind label');
	assert.match(source, /<span class="monitor-gpu-row__hold-owner">\{primaryHoldDisplayName\}<\/span>/, 'owner label should remain separate from the HOLD kind');
	assert.match(source, /monitor-gpu-row__hold-kind[\s\S]*monitor-gpu-row__hold-owner/, 'visible cue order should read HOLD before the display name');
	assert.doesNotMatch(source, /<span class="monitor-gpu-row__hold-owner">HOLD \{primaryHoldDisplayName\}<\/span>/, 'owner label should not be mutated into a synthetic HOLD username');
});

test('GpuBar preserves telemetry truth in aria-label while adding advisory hold detail', () => {
	const oneLine = normalized();
	assert.match(
		oneLine,
		/GPU \$\{gpu\.index\}.*users \$\{usage\}.*utilization \$\{utilValue\} percent.*memory \$\{memUsedGB\} of \$\{memTotalGB\} gigabytes/,
		'aria label must retain telemetry users/utilization/memory'
	);
	assert.match(source, /holdAriaDetail/, 'aria label should include advisory hold details when present');
	assert.match(source, /resolveDisplayName\(entry\.note\)|resolveDisplayName\(note\)/, 'aria detail should use the shared display fallback');
});


test('GpuBar exposes a stable tooltip id that includes server scope', () => {
	assert.match(source, /primaryHold\.server_id/);
	assert.match(source, /gpu-hold-tooltip-\$\{primaryHold\.server_id\}-\$\{gpu\.index\}/);
	assert.doesNotMatch(source, /`gpu-hold-tooltip-\$\{gpu\.index\}`/);
});
test('GpuBar exposes a custom accessible tooltip on hover and focus instead of title-only hints', () => {
	assert.match(source, /let\s+tooltipOpen\s*=\s*\$state\(false\)/);
	assert.match(source, /function\s+openTooltip\(\)\s*\{/);
	assert.match(source, /function\s+closeTooltip\(\)\s*\{/);
	assert.match(source, /function\s+handleTooltipKeydown\(event:\s*KeyboardEvent\)\s*\{/);
	assert.match(source, /event\.key\s*===\s*'Escape'/);
	assert.match(source, /<button[\s\S]*type="button"[\s\S]*class=\{`monitor-gpu-row__hold-cue/);
	assert.match(source, /onmouseenter=\{openTooltip\}/);
	assert.match(source, /onmouseleave=\{closeTooltip\}/);
	assert.match(source, /onfocus=\{openTooltip\}/);
	assert.match(source, /onblur=\{closeTooltip\}/);
	assert.match(source, /onkeydown=\{handleTooltipKeydown\}/);
	assert.match(source, /role="tooltip"/);
	assert.doesNotMatch(source, /class="monitor-gpu-row__hold-cue"[^>]*title=/, 'GpuBar cue must not rely on title-only hints');
});

test('GpuBar tooltip content includes gpu identity, resolved display name, priority, expiry, memo summary, and semantic priority styles', () => {
	assert.match(source, /GPU G\{gpu\.index\} · \{gpu\.name\}/);
	assert.match(source, /\{resolveDisplayName\(entry\.note\)\}|\{resolveDisplayName\(note\)\}/);
	assert.match(source, /\{priorityMeta\.label\}/);
	assert.match(source, /\{entry\.remaining\}/);
	assert.match(source, /\{entry\.note\.content\}/);
	const gpuListRule = cssRule('.monitor-card__gpu-list');
	assertDeclaration(gpuListRule, 'padding-top', '0.4rem');
	assert.match(cardCss, /\.monitor-gpu-row__hold-cue\.note-priority--normal[\s\S]*color:/, 'normal cues should stay neutral');
	assert.match(cardCss, /\.monitor-gpu-row__hold-cue\.note-priority--high[\s\S]*color:/, 'high cues should use a semantic emphasis color');
	assert.match(cardCss, /\.monitor-gpu-row__hold-cue\.note-priority--urgent[\s\S]*color:/, 'urgent cues should use a stronger semantic emphasis color');
});

test('GpuBar sorts display users, keys a height-stable identity slot, and flies identity changes with reduced-motion fallback', () => {
	assert.match(source, /displayUsers\s*=\s*\$derived\.by\(\(\)\s*=>\s*\[\.\.\.gpu\.users\]\.sort\(\)\)/);
	assert.ok(source.includes("const displayUsersSignature = $derived(displayUsers.join('\\u0000') || 'idle');"));
	assert.match(source, /import\s*\{\s*prefersReducedMotion\s*\}\s*from\s*'svelte\/motion'/);
	assert.match(source, /import\s*\{\s*cubicOut\s*\}\s*from\s*'svelte\/easing'/);
	assert.match(source, /import\s*\{\s*fly\s*\}\s*from\s*'svelte\/transition'/);
	assert.match(source, /const identityInFly\s*=\s*\$derived\(\{[\s\S]*y:\s*prefersReducedMotion\.current\s*\?\s*0\s*:\s*2,[\s\S]*opacity:\s*prefersReducedMotion\.current\s*\?\s*1\s*:\s*0,[\s\S]*duration:\s*prefersReducedMotion\.current\s*\?\s*0\s*:\s*220,[\s\S]*easing:\s*cubicOut[\s\S]*\}\)/);
	assert.match(source, /const identityOutFly\s*=\s*\$derived\(\{[\s\S]*y:\s*prefersReducedMotion\.current\s*\?\s*0\s*:\s*-2,[\s\S]*opacity:\s*prefersReducedMotion\.current\s*\?\s*1\s*:\s*0,[\s\S]*duration:\s*prefersReducedMotion\.current\s*\?\s*0\s*:\s*160,[\s\S]*easing:\s*cubicOut[\s\S]*\}\)/);
	assert.match(source, /class="monitor-gpu-row__identity-slot"/);
	assert.match(source, /\{#key displayUsersSignature\}[\s\S]*class="monitor-gpu-row__identity-set"[\s\S]*in:fly=\{identityInFly\}[\s\S]*out:fly=\{identityOutFly\}/);
	assert.match(source, /\{#each displayUsers as user, index \(`/);
	assert.doesNotMatch(source, /\{#each gpu\.users as user, index/);
});
