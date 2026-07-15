// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./GpuBar.svelte', import.meta.url), 'utf8');

function normalized() {
	return source.replace(/\s+/g, ' ');
}

test('GpuBar accepts advisory hold cues without changing telemetry activity semantics', () => {
	assert.match(source, /advisoryHolds\s*=\s*\[\]/, 'GpuBar should accept default-empty advisory hold cues');
	assert.match(source, /const\s+isActive\s*=\s*\$derived\(gpu\.users\.length\s*>\s*0\)/, 'active state must remain based only on telemetry users');
	assert.match(source, /data-active=\{isActive\s*\?\s*'true'\s*:\s*'false'\}/, 'data-active should remain wired to telemetry activity only');
	assert.doesNotMatch(source, /const\s+isActive[^{;]*(?:advisoryHolds|hold)/, 'advisory holds must not alter active/availability semantics');
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
