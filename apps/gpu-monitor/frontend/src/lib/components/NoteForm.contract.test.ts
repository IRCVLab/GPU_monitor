// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./NoteForm.svelte', import.meta.url), 'utf8');

test('NoteForm keeps the exact memo/hold props, stale helper, and advisory copy', () => {
	assert.match(source, /let \{\s*serverId,\s*gpus,\s*serverStatus,\s*lastSeen,\s*onCreated\s*\}(?::\s*NoteFormProps)?\s*=\s*\$props\(\);/s);
	assert.match(source, /isTelemetryStale\(lastSeen, nowMs, \d+\)/);
	assert.match(source, /toggleGpu/);
	assert.match(source, /advisory soft hold/);
	assert.match(source, /buildNotePayload\(/);
	assert.doesNotMatch(source, /exclusive|reserved|cancelled_at/i);
});

test('NoteForm resets GPU selection for memo mode and after successful hold creation', () => {
	assert.match(source, /kind\s*=\s*'memo';\s*selectedGpuIndices\s*=\s*\[\]/);
	assert.match(source, /onCreated\(note\);[\s\S]*selectedGpuIndices\s*=\s*\[\]/);
});

test('NoteForm warns for offline or stale telemetry without blocking submission', () => {
	assert.match(source, /serverStatus\s*!==\s*'online'/);
	assert.match(source, /telemetryStale/);
	assert.doesNotMatch(source, /if \([^)]*(serverStatus|telemetryStale)[^)]*\)\s*return/);
});
