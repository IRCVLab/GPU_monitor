// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./ServerCard.svelte', import.meta.url), 'utf8');

test('ServerCard renders advisory hold chips and wires the new NoteForm props', () => {
	assert.match(source, /note\.kind === 'hold'/);
	assert.match(source, /note\.gpu_indices/);
	assert.match(source, /monitor-note-item__kind">HOLD<\/span>/);
	assert.match(source, /<NoteForm[\s\S]*serverId=\{server\.server_id\}[\s\S]*gpus=\{server\.gpus\}[\s\S]*serverStatus=\{server\.status\}[\s\S]*lastSeen=\{server\.last_seen\}[\s\S]*onCreated=\{onNoteCreated\}/);
});

test('ServerCard derives active unexpired hold notes per GPU and passes cues into GpuBar', () => {
	assert.match(source, /activeHoldNotesByGpu\s*=\s*\$derived\.by/, 'missing derived per-GPU hold cue map');
	assert.match(source, /note\.kind\s*===\s*'hold'/, 'hold cue map should only use hold notes');
	assert.match(source, /noteVisible\(note\)/, 'hold cue map should ignore expired notes');
	assert.match(source, /holdGpuIndices\(note\)/, 'hold cue map should validate GPU indices');
	const gpuBar = source.match(/<GpuBar[\s\S]*?\/>/)?.[0] ?? '';
	assert.match(gpuBar, /\{gpu\}/, 'GPU row should receive its telemetry record');
	assert.match(gpuBar, /state=\{getCompactGpuState\(server\.status, server\.last_seen, gpu\)\}/, 'GPU row should receive the shared availability state');
	assert.match(gpuBar, /advisoryHolds=\{activeHoldNotesByGpu\[gpu\.index\]\s*\?\?\s*\[\]\}/, 'each GPU row should receive only its own hold cues');
});

test('ServerCard note preview and history use concise HOLD marker instead of long advisory phrase', () => {
	assert.doesNotMatch(source, /advisory soft hold/);
	assert.match(source, /monitor-note-item__kind">HOLD<\/span>/);
	assert.match(source, /monitor-note-item__gpu-chip">G\{gpuIndex\}<\/span>/);
});

test('ServerCard separates memo history from the composer and provides a deliberate empty state', () => {
	assert.match(source, /monitor-card__memo-group monitor-card__memo-group--history/);
	assert.match(source, /monitor-card__memo-group monitor-card__memo-group--composer/);
	assert.match(source, /monitor-card__memo-group-title">기록<\/span>/);
	assert.match(source, /monitor-card__memo-group-title">작성<\/span>/);
	assert.match(source, /monitor-card__memo-empty/);
});

test('collapsed System and Memo controls use structural markers and CSS disclosure angles', () => {
	assert.match(source, /monitor-card__footer-marker monitor-card__footer-marker--system/);
	assert.match(source, /monitor-card__footer-marker monitor-card__footer-marker--memo/);
	assert.match(source, /monitor-card__footer-disclosure/);
	assert.doesNotMatch(source, />\s*[▾▸]\s*</);
});

test('ServerCard hides the normal status label while keeping exception labels visible', () => {
	assert.match(source, /statusMeta\.label === '정상'/);
	assert.match(source, /class="monitor-card__status-text monitor-card__sr-only"/);
	assert.match(source, /\{:else\}\s*<span class="monitor-card__status-text">\{statusMeta\.label\}<\/span>/);
});

test('ServerCard renders collapsed system preview as four named micro-items', () => {
	assert.match(source, /monitor-card__system-preview/);
	assert.match(source, /monitor-card__system-preview-item/);
	assert.match(source, /<small>CPU<\/small>[\s\S]*?<strong[^>]*>\{server\.system \? `\$\{cpuPct\.toFixed\(0\)\}%` : '–'\}<\/strong>/);
	assert.match(source, /<small>RAM<\/small>[\s\S]*?<strong[^>]*>\{server\.system \? `\$\{ramPct\.toFixed\(0\)\}%` : '–'\}<\/strong>/);
	assert.match(source, /<small>GPU<\/small>[\s\S]*?<strong[^>]*>\{totalGpuPowerText\}<\/strong>/);
	assert.match(source, /<small>Disk<\/small>[\s\S]*?<strong[^>]*>\{storageSummary \? `\$\{storagePct\.toFixed\(0\)\}%` : '–'\}<\/strong>/);
	assert.doesNotMatch(source, /aria-label="시스템 요약"/);
	assert.match(source, /title=\{`CPU \$\{cpuPreviewText\}`\}/);
	assert.match(source, /title=\{`RAM \$\{server\.system \? `\$\{ramPct\.toFixed\(0\)\}%` : '–'\}`\}/);
	assert.match(source, /title=\{`GPU \$\{totalGpuPowerText\}`\}/);
	assert.match(source, /title=\{`Disk \$\{diskPreviewText\}`\}/);
	assert.doesNotMatch(source, /segments\.join\(' · '\)/);
});
