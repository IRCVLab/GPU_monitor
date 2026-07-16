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
	assert.match(source, /state:\s*CompactGpuState/);
	assert.match(source, /data-state=\{state\}/);
	assert.doesNotMatch(source, /data-active=/);
	assert.match(serverCardSource, /getCompactGpuState\(server\.status, server\.last_seen, gpu\)/);
	assert.match(serverCardSource, /<GpuBar[\s\S]*state=\{operationalState === 'impaired' \? staleAvailabilityState : getCompactGpuState\(server\.status, server\.last_seen, gpu\)\}/);
	assert.match(serverCardSource, /const staleAvailabilityState = \$derived\('unknown'\)/, 'impaired server cards should preserve unknown availability');
	assert.doesNotMatch(source, /state\s*=.*(?:advisoryHolds|hold)/, 'advisory holds must not alter availability semantics');
});

test('GpuBar keeps visible HOLD owner text and a dedicated held index marker', () => {
	assert.match(source, /monitor-gpu-row__hold-cue/, 'missing dense hold cue element');
	assert.match(source, />\s*HOLD\s*\{primaryHold\.owner\}/, 'hold cue should include visible HOLD owner text');
	assert.match(
		source,
		/class="monitor-gpu-row__index" data-has-hold=\{primaryHold \? 'true' : 'false'\}/,
		'the exact G# chip should advertise when it carries a HOLD collar'
	);
	assert.match(
		source,
		/advisoryHolds\.length\s*>\s*1[\s\S]*\+\{advisoryHolds\.length\s*-\s*1\}/,
		'multiple holds should collapse to a concise count'
	);
	assert.doesNotMatch(source, /monitor-gpu-row__hold-cue[^>]*(?:<button|<a|tabindex=)/, 'hold cue should be noninteractive');
});

test('GpuBar preserves telemetry truth in aria-label while adding advisory hold detail', () => {
	const oneLine = normalized();
	assert.match(
		oneLine,
		/GPU \$\{gpu\.index\}.*users \$\{usage\}.*utilization \$\{utilValue\} percent.*memory \$\{memUsedGB\} of \$\{memTotalGB\} gigabytes/,
		'aria label must retain telemetry users/utilization/memory'
	);
	assert.match(source, /holdAriaDetail/, 'aria label should include advisory hold details when present');
	assert.match(source, /title=\{holdDetailText\}|aria-label=\{holdDetailText\}/, 'hold cue should expose owner/time/memo detail accessibly');
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
