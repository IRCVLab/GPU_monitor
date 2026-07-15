// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./NoteForm.svelte', import.meta.url), 'utf8');

test('NoteForm keeps server props, stale helper, payload validation, and Korean advisory copy', () => {
	assert.match(source, /let \{\s*serverId,\s*gpus,\s*serverStatus,\s*lastSeen,\s*onCreated\s*\}(?::\s*NoteFormProps)?\s*=\s*\$props\(\);/s);
	assert.match(source, /isTelemetryStale\(lastSeen, nowMs, \d+\)/);
	assert.match(source, /toggleGpu/);
	assert.match(source, /buildNotePayload\(/);
	assert.match(source, /자문|참고|안내/);
	assert.match(source, /비독점|독점이 아니/);
	assert.match(source, /텔레메트리/);
	assert.doesNotMatch(source, /reserved|cancelled_at/i);
});

test('NoteForm derives memo versus hold from selected GPUs and resets after successful creation', () => {
	assert.doesNotMatch(source, /note-form-kind-row|note-form-kind-toggle|setKind\s*\(/);
	assert.doesNotMatch(source, /let\s+kind\s*=\s*\$state/);
	assert.match(source, /kind:\s*selectedGpuIndices\.length\s*>\s*0\s*\?\s*'hold'\s*:\s*'memo'/);
	assert.match(source, /gpu_indices:\s*selectedGpuIndices/);
	assert.match(source, /onCreated\(note\);[\s\S]*content\s*=\s*''[\s\S]*expiresAtLocal\s*=\s*defaultExpiryLocal\(\)[\s\S]*selectedGpuIndices\s*=\s*\[\]/);
});

test('NoteForm warns for offline or stale telemetry without blocking submission', () => {
	assert.match(source, /serverStatus\s*!==\s*'online'/);
	assert.match(source, /telemetryStale/);
	assert.doesNotMatch(source, /if \([^)]*(serverStatus|telemetryStale)[^)]*\)\s*return/);
});


test('NoteForm always renders accessible GPU selector before submit and only selected GPUs reveal guidance', () => {
	assert.match(source, /role="group"\s+aria-label="GPU 선택/);
	assert.match(source, /aria-pressed=\{selectedGpuIndices\.includes\(gpu\.index\)\}/);
	assert.doesNotMatch(source, /\{#if\s+kind\s*===\s*['"]hold['"]\}/);
	assert.match(source, /\{#if\s+selectedGpuIndices\.length\s*>\s*0\}/);
	assert.match(source, /\{#if\s+selectedGpuIndices\.length\s*>\s*0\s*&&\s*\(telemetryStale\s*\|\|\s*statusWarning\)\}/);
});
