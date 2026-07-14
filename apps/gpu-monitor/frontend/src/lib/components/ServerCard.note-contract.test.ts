// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./ServerCard.svelte', import.meta.url), 'utf8');

test('ServerCard renders advisory hold chips and wires the new NoteForm props', () => {
	assert.match(source, /note\.kind === 'hold'/);
	assert.match(source, /note\.gpu_indices/);
	assert.match(source, /advisory soft hold/);
	assert.match(source, /<NoteForm[\s\S]*serverId=\{server\.server_id\}[\s\S]*gpus=\{server\.gpus\}[\s\S]*serverStatus=\{server\.status\}[\s\S]*lastSeen=\{server\.last_seen\}[\s\S]*onCreated=\{onNoteCreated\}/);
});
