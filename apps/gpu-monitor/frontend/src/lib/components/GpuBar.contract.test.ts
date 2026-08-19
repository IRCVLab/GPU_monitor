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
	assert.match(source, /const\s+priorityNudge\s*=\s*\$derived\(primaryHold\?\.priority === 'urgent' \? '!!' : primaryHold\?\.priority === 'high' \? '!' : ''\)/);
	assert.match(
		source,
		/\{holdAdvisory\.secondarySummary\}/,
		'multiple holds should collapse to a concise count'
	);
});

test('GpuBar cue keeps the concise HOLD owner prefix and uses compact urgency nudges', () => {
	assert.match(source, /<span class="monitor-gpu-row__hold-kind">HOLD<\/span>/, 'full GPU cue should identify the advisory as HOLD');
	assert.match(source, /<span class="monitor-gpu-row__hold-owner">\{primaryHoldDisplayName\}<\/span>/, 'owner label should remain separate from the HOLD kind');
	assert.match(source, /monitor-gpu-row__hold-kind[\s\S]*monitor-gpu-row__hold-owner[\s\S]*\{#if priorityNudge\}[\s\S]*monitor-gpu-row__hold-nudge[\s\S]*\{priorityNudge\}/, 'visible cue should read HOLD, owner, then a compact urgency nudge');
	assert.doesNotMatch(source, /monitor-gpu-row__hold-priority/, 'full GPU cue should not repeat a textual priority label');
});

test('GpuBar preserves telemetry truth in aria-label while adding only a concise advisory summary', () => {
	const oneLine = normalized();
	assert.match(source, /<div class="monitor-gpu-row" role="group"/, 'telemetry row must expose its concise accessible name on an explicit group role');
	assert.match(
		oneLine,
		/GPU \$\{gpu\.index\}.*users \$\{usage\}.*utilization \$\{utilValue\} percent.*memory \$\{memUsedGB\} of \$\{memTotalGB\} gigabytes/,
		'aria label must retain telemetry users/utilization/memory'
	);
	assert.match(source, /compactHoldAriaText/, 'aria label should include the compact hold summary when present');
	assert.match(source, /primaryHoldDisplayName/, 'aria summary should use the shared display fallback');
	assert.doesNotMatch(source, /entry\.note\.content/, 'GPU row aria must not repeat memo bodies');
});

test('GpuBar renders a static compact HOLD summary without interactive or duplicate detail UI', () => {
	assert.match(source, /<span[\s\S]*class=\{`monitor-gpu-row__hold-cue/);
	assert.match(source, /class=\{`monitor-gpu-row__hold-cue[\s\S]*aria-hidden="true"/, 'visible cue should not duplicate the row-level accessible summary');
	assert.doesNotMatch(source, /<button[\s\S]*class=\{`monitor-gpu-row__hold-cue/);
	assert.doesNotMatch(source, /tooltipOpen|tooltipId|openTooltip|closeTooltip|handleTooltipKeydown/);
	assert.doesNotMatch(source, /onmouseenter=|onmouseleave=|onfocus=|onblur=|onkeydown=/);
	assert.doesNotMatch(source, /role="tooltip"|monitor-gpu-row__tooltip/);
	assert.doesNotMatch(source, /GPU G\{gpu\.index\} · \{gpu\.name\}/, 'hold cue must not repeat GPU identity or model');
	assert.doesNotMatch(source, /entry\.remaining|entry\.note\.content/, 'hold cue must leave expiry and memo bodies to the Memo panel');
	assert.match(source, /monitor-gpu-row__hold-kind[\s\S]*monitor-gpu-row__hold-owner[\s\S]*monitor-gpu-row__hold-nudge/, 'cue should keep HOLD and owner primary with urgency secondary');
	assert.match(source, /\{holdAdvisory\.secondarySummary\}/, 'multiple holds should remain summarized as +N');
	assert.match(cssRule('.monitor-gpu-row__hold-cue'), /pointer-events:\s*none/, 'static cue must not advertise a hover interaction');
	assert.doesNotMatch(cardCss, /\.monitor-gpu-row__tooltip(?:\s|\{|[-_])/, 'obsolete tooltip styling should be removed');

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
