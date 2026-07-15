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
	assert.match(serverCardSource, /<GpuBar[\s\S]*state=\{getCompactGpuState\(server\.status, server\.last_seen, gpu\)\}/);
	assert.doesNotMatch(source, /state\s*=.*(?:advisoryHolds|hold)/, 'advisory holds must not alter availability semantics');
});

test('GpuBar renders compact visible noninteractive HOLD text beside identity line', () => {
	assert.match(source, /monitor-gpu-row__hold-cue/, 'missing dense hold cue element');
	assert.match(source, />\s*HOLD\s*\{/, 'hold cue should include visible HOLD text, not color alone');
	assert.match(source, /advisoryHolds\.length\s*>\s*1[\s\S]*\+\{advisoryHolds\.length\s*-\s*1\}/, 'multiple holds should collapse to a concise count');
	assert.doesNotMatch(source, /monitor-gpu-row__hold-cue[^>]*(?:<button|<a|tabindex=)/, 'hold cue should be noninteractive');
});

test('GpuBar preserves telemetry truth in aria-label while adding advisory hold detail', () => {
	const oneLine = normalized();
	assert.match(oneLine, /GPU \$\{gpu\.index\}.*users \$\{usage\}.*utilization \$\{utilValue\} percent.*memory \$\{memUsedGB\} of \$\{memTotalGB\} gigabytes/, 'aria label must retain telemetry users/utilization/memory');
	assert.match(source, /holdAriaDetail/, 'aria label should include advisory hold details when present');
	assert.match(source, /title=\{holdDetailText\}|aria-label=\{holdDetailText\}/, 'hold cue should expose owner/time/memo detail accessibly');
});
