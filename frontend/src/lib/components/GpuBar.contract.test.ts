// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./GpuBar.svelte', import.meta.url), 'utf8');
const serverCardSource = readFileSync(new URL('./ServerCard.svelte', import.meta.url), 'utf8');

function normalized() {
	return source.replace(/\s+/g, ' ');
}

test('GPU memory metric retains the compact GB value', () => {
	assert.match(source, /\{memUsedGB\}\/\{memTotalGB\}GB/);
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
