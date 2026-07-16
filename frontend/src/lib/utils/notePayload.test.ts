// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildNotePayload } from './notePayload.ts';

test('memo payload rejects GPU indices and defaults priority to normal', () => {
	assert.deepEqual(
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'memo',
			expires_at: '2026-07-15T00:00:00Z'
		}),
		{
			username: 'u',
			display_name: null,
			priority: 'normal',
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
			display_name: null,
			priority: 'normal',
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
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: [1.5]
		});
	}, /gpu_indices must contain non-negative integers/);
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: [true as unknown as number]
		});
	}, /gpu_indices must contain non-negative integers/);
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: ['2' as unknown as number]
		});
	}, /gpu_indices must contain non-negative integers/);
});

test('payload trims display names, normalizes blanks to null, and does not fall back to username', () => {
	assert.deepEqual(
		buildNotePayload({
			username: '  owner-login  ',
			display_name: '  Grace Hopper  ',
			priority: 'high',
			ssh_password: '  pw  ',
			content: '  reserve me  ',
			expires_at: '2026-07-16T00:00:00Z'
		}),
		{
			username: 'owner-login',
			display_name: 'Grace Hopper',
			priority: 'high',
			ssh_password: 'pw',
			content: 'reserve me',
			expires_at: '2026-07-16T00:00:00Z',
			kind: 'memo',
			gpu_indices: []
		}
	);

	const blankDisplayNamePayload = buildNotePayload({
		username: 'owner-login',
		display_name: '   ',
		ssh_password: 'pw',
		content: 'memo',
		expires_at: '2026-07-16T00:00:00Z'
	});
	assert.equal(blankDisplayNamePayload.display_name, null);
	assert.notEqual(blankDisplayNamePayload.display_name, blankDisplayNamePayload.username);
});

test('payload rejects display names longer than 40 characters', () => {
	assert.throws(() => {
		buildNotePayload({
			username: 'owner-login',
			display_name: 'x'.repeat(41),
			ssh_password: 'pw',
			content: 'memo',
			expires_at: '2026-07-16T00:00:00Z'
		});
	}, /display_name must be at most 40 characters/);
});
