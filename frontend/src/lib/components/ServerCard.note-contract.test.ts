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
	assert.match(source, /<GpuBar\s+\{gpu\}\s+advisoryHolds=\{activeHoldNotesByGpu\[gpu\.index\]\s*\?\?\s*\[\]\}\s*\/>/, 'each GPU row should receive only its own hold cues');
});

test('ServerCard note preview and history use concise HOLD marker instead of long advisory phrase', () => {
	assert.doesNotMatch(source, /advisory soft hold/);
	assert.match(source, /monitor-note-item__kind">HOLD<\/span>/);
	assert.match(source, /monitor-note-item__gpu-chip">G\{gpuIndex\}<\/span>/);
});
