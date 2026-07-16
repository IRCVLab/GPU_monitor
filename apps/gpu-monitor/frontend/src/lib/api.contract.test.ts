// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const apiUrl = new URL('./api.ts', import.meta.url);
const typesUrl = new URL('./types.ts', import.meta.url);
const notePayloadUrl = new URL('./utils/notePayload.ts', import.meta.url);
const apiSource = readFileSync(apiUrl, 'utf8');
const typesSource = readFileSync(typesUrl, 'utf8');

async function loadApiModule() {
	const tempDir = mkdtempSync(join(tmpdir(), 'monitoring-api-contract-'));
	mkdirSync(join(tempDir, 'utils'));
	writeFileSync(join(tempDir, 'types.ts'), readFileSync(typesUrl, 'utf8'));
	writeFileSync(join(tempDir, 'utils', 'notePayload.ts'), readFileSync(notePayloadUrl, 'utf8'));
	writeFileSync(
		join(tempDir, 'api.ts'),
		apiSource
			.replace("from '$lib/types'", "from './types.ts'")
			.replace("from '$lib/utils/notePayload'", "from './utils/notePayload.ts'")
	);
	return import(`${pathToFileURL(join(tempDir, 'api.ts')).href}?t=${Date.now()}-${Math.random()}`);
}

test('API exposes the exact note payload shape and createNote signature', () => {
	assert.match(typesSource, /export type NotePriority = 'normal' \| 'high' \| 'urgent';/);
	assert.match(typesSource, /kind: NoteKind;/);
	assert.match(typesSource, /gpu_indices: number\[];/);
	assert.match(typesSource, /display_name: string \| null;/);
	assert.match(typesSource, /priority: NotePriority;/);
	assert.match(apiSource, /export async function createNote\(serverId: number, input: CreateNoteInput\): Promise<Note>/);
	assert.match(apiSource, /buildNotePayload\(input\)/);
});

test('getNotes tolerates malformed legacy priority and display_name values without breaking note loading', async (t) => {
	const { getNotes } = await loadApiModule();
	const originalFetch = globalThis.fetch;
	const legacyLongDisplayName = `  ${'Legacy operator name from old API payload'.repeat(2)}  `;
	globalThis.fetch = async () =>
		new Response(
			JSON.stringify([
				{
					id: 1,
					server_id: 9,
					username: 'owner-a',
					content: 'legacy missing fields',
					created_at: '2026-07-15T00:00:00Z',
					expires_at: null
				},
				{
					id: 2,
					server_id: 9,
					username: 'owner-b',
					display_name: null,
					content: 'null display',
					created_at: '2026-07-15T00:10:00Z',
					expires_at: null,
					priority: 'high'
				},
				{
					id: 3,
					server_id: 9,
					username: 'owner-c',
					display_name: '',
					content: 'empty display',
					created_at: '2026-07-15T00:20:00Z',
					expires_at: null,
					priority: 'urgent'
				},
				{
					id: 4,
					server_id: 9,
					username: 'owner-d',
					display_name: '   ',
					content: 'blank display',
					created_at: '2026-07-15T00:30:00Z',
					expires_at: null,
					priority: 'normal'
				},
				{
					id: 5,
					server_id: 9,
					username: 'owner-e',
					display_name: 12345,
					content: 'non-string display',
					created_at: '2026-07-15T00:40:00Z',
					expires_at: null,
					priority: 'legacy'
				},
				{
					id: 6,
					server_id: 9,
					username: 'owner-f',
					display_name: legacyLongDisplayName,
					content: 'overlong display',
					created_at: '2026-07-15T00:50:00Z',
					expires_at: null,
					priority: 'unknown'
				}
			]),
			{ status: 200, headers: { 'Content-Type': 'application/json' } }
		);
	t.after(() => {
		globalThis.fetch = originalFetch;
	});

	const notes = await getNotes(9);

	assert.deepEqual(
		notes.map(({ id, display_name, priority, kind, gpu_indices }) => ({
			id,
			display_name,
			priority,
			kind,
			gpu_indices
		})),
		[
			{ id: 1, display_name: null, priority: 'normal', kind: 'memo', gpu_indices: [] },
			{ id: 2, display_name: null, priority: 'high', kind: 'memo', gpu_indices: [] },
			{ id: 3, display_name: null, priority: 'urgent', kind: 'memo', gpu_indices: [] },
			{ id: 4, display_name: null, priority: 'normal', kind: 'memo', gpu_indices: [] },
			{ id: 5, display_name: null, priority: 'normal', kind: 'memo', gpu_indices: [] },
			{
				id: 6,
				display_name: legacyLongDisplayName.trim().slice(0, 40),
				priority: 'normal',
				kind: 'memo',
				gpu_indices: []
			}
		]
	);
});
