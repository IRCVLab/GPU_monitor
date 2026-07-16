// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./NoteForm.svelte', import.meta.url), 'utf8');

test('NoteForm pauses its one-second ticker when inactive and preserves active behavior', () => {
	assert.match(source, /active\??:\s*boolean/, 'NoteForm should accept an active boolean prop');
	assert.match(source, /active\s*=\s*true/, 'NoteForm should default active to true for existing callers');
	const effect = source.match(/\$effect\(\(\) => \{[\s\S]*?return \(\) => clearInterval\(timer\);[\s\S]*?\}\);/)?.[0] ?? '';
	assert.match(effect, /if \(!active\) return;/, 'inactive mounted forms must not start a ticker');
	assert.match(effect, /setInterval\(\(\) => \{[\s\S]*nowMs\s*=\s*Date\.now\(\)/, 'active forms should keep the existing one-second nowMs ticker');
	assert.match(effect, /clearInterval\(timer\)/, 'active ticker should still clean up');
});
test('NoteForm keeps server props, stale helper, payload validation, and Korean advisory copy', () => {
	assert.match(source, /let \{\s*serverId,\s*gpus,\s*serverStatus,\s*lastSeen,\s*active\s*=\s*true,\s*onCreated\s*\}(?::\s*NoteFormProps)?\s*=\s*\$props\(\);/s);
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
	const submit = source.match(new RegExp('async function handleSubmit\\(\\) \\{[\\s\\S]*?\\n\\t\\}'))?.[0] ?? '';
	assert.doesNotMatch(submit, /(serverStatus|telemetryStale|statusWarning)/);
});


test('NoteForm always renders accessible GPU selector before submit and only selected GPUs reveal guidance', () => {
	assert.match(source, /role="group"\s+aria-label="GPU 선택/);
	assert.match(source, /aria-pressed=\{selectedGpuIndices\.includes\(gpu\.index\)\}/);
	assert.doesNotMatch(source, /\{#if\s+kind\s*===\s*['"]hold['"]\}/);
	assert.match(source, /\{#if\s+selectedGpuIndices\.length\s*>\s*0\s*&&\s*\(telemetryStale\s*\|\|\s*statusWarning\)\}/);
});


test('NoteForm removes the always-visible hold explainer and stays within three compact composer rows', () => {
	assert.doesNotMatch(source, /note-form-hold-copy/);
	assert.doesNotMatch(source, /showPrecisePicker/);
	assert.doesNotMatch(source, /note-form-expiry-summary-row/);
	assert.match(source, /class="note-form-row note-form-scope-row"/);
	assert.match(source, /class="note-form-row note-form-entry-row"/);
	assert.match(source, /class="note-form-row note-form-submit-row"/);
	assert.match(source, /class="note-form-identity-stack"/);
	assert.match(source, /type="datetime-local"/);
});

test('NoteForm keeps the hold warning conditional to stale or abnormal telemetry only', () => {
	assert.doesNotMatch(source, /선택한 GPU는 비독점 참고 홀드입니다/);
	assert.match(source, /\{#if\s+selectedGpuIndices\.length\s*>\s*0\s*&&\s*\(telemetryStale\s*\|\|\s*statusWarning\)\}/);
	assert.match(source, /참고 안내로만 사용하세요/);
});


test('NoteForm expiry summary uses concise active relative units without 남음', () => {
	assert.ok(source.includes('return `${seconds}초`;'));
	assert.ok(source.includes('return `${minutes}분`;'));
	assert.ok(source.includes('return `${hours}시간`;'));
	assert.ok(source.includes('return `${days}일`;'));
	assert.doesNotMatch(source, /`\$\{(?:seconds|minutes|hours|days)\}(?:초|분|시간|일) 남음`/);
	assert.match(source, /\$\{formatExpiryAbsolute\(expiresAtDate\)\} · \$\{formatRemaining\(diffMs\)\}/);
});
