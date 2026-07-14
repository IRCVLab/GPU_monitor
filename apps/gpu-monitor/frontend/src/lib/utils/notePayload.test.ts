// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildNotePayload } from './notePayload.ts';

test('memo payload rejects GPU indices and keeps plain memo default', () => {
	assert.deepEqual(
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'memo',
			expires_at: '2026-07-15T00:00:00Z'
		}),
		{
			username: 'u',
			ssh_password: 'pw',
			content: 'memo',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'memo',
			gpu_indices: []
		}
	);
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'memo',
			expires_at: '2026-07-15T00:00:00Z',
			gpu_indices: [1]
		});
	}, /memo notes cannot include gpu indices/);
});

test('hold payload sorts unique indices and throws on empty, negative, or noninteger values', () => {
	assert.deepEqual(
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: [3, 1, 3, 2]
		}),
		{
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: [1, 2, 3]
		}
	);
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: []
		});
	}, /hold notes require at least one gpu index/);
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: [-1]
		});
	}, /gpu_indices must contain non-negative integers/);
});
