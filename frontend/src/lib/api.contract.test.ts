// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const apiSource = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
const typesSource = readFileSync(new URL('./types.ts', import.meta.url), 'utf8');

test('API exposes the exact note payload shape and createNote signature', () => {
	assert.match(typesSource, /export type NoteKind = 'memo' \| 'hold';/);
	assert.match(typesSource, /kind: NoteKind;/);
	assert.match(typesSource, /gpu_indices: number\[];/);
	assert.match(apiSource, /export async function createNote\(serverId: number, input: CreateNoteInput\): Promise<Note>/);
	assert.match(apiSource, /buildNotePayload\(input\)/);
});
