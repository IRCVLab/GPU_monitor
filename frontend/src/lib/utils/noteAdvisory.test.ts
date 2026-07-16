// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';

import {
	buildHoldAdvisory,
	formatAdditionalHoldSummary,
	getNotePriorityMeta,
	rankHoldNotes,
	resolveDisplayName
} from './noteAdvisory.ts';

function makeHoldNote(overrides = {}) {
	return {
		id: 1,
		server_id: 9,
		username: 'owner',
		display_name: null,
		content: 'reserved',
		created_at: '2026-07-15T00:00:00Z',
		expires_at: '2026-07-16T00:00:00Z',
		priority: 'normal',
		kind: 'hold',
		gpu_indices: [0],
		...overrides
	};
}

test('resolveDisplayName falls back to username when raw display_name is null or blank', () => {
	assert.equal(resolveDisplayName(makeHoldNote({ username: 'gpu-owner', display_name: 'Grace Hopper' })), 'Grace Hopper');
	assert.equal(resolveDisplayName(makeHoldNote({ username: 'gpu-owner', display_name: null })), 'gpu-owner');
	assert.equal(resolveDisplayName(makeHoldNote({ username: 'gpu-owner', display_name: '   ' })), 'gpu-owner');
});

test('priority metadata returns semantic labels and classes', () => {
	assert.deepEqual(getNotePriorityMeta('normal'), { label: '보통', className: 'note-priority--normal' });
	assert.deepEqual(getNotePriorityMeta('high'), { label: '높음', className: 'note-priority--high' });
	assert.deepEqual(getNotePriorityMeta('urgent'), { label: '긴급', className: 'note-priority--urgent' });
});

test('hold ranking is deterministic by priority, soonest expiry, and stable tie-break', () => {
	const sameExpiryFirst = makeHoldNote({ id: 21, priority: 'urgent', expires_at: '2026-07-15T02:00:00Z' });
	const normalSoon = makeHoldNote({ id: 22, priority: 'normal', expires_at: '2026-07-15T01:00:00Z' });
	const urgentEarlier = makeHoldNote({ id: 23, priority: 'urgent', expires_at: '2026-07-15T01:00:00Z' });
	const highSoon = makeHoldNote({ id: 24, priority: 'high', expires_at: '2026-07-15T00:30:00Z' });
	const sameExpirySecond = makeHoldNote({ id: 25, priority: 'urgent', expires_at: '2026-07-15T02:00:00Z' });
	const memoNoise = { ...makeHoldNote({ id: 26, kind: 'memo', priority: 'urgent' }), gpu_indices: [] };

	const ranked = rankHoldNotes([
		sameExpiryFirst,
		normalSoon,
		sameExpirySecond,
		urgentEarlier,
		highSoon,
		memoNoise
	]);

	assert.deepEqual(
		ranked.map((note) => note.id),
		[23, 21, 25, 24, 22]
	);
});

test('hold advisory returns the primary hold and +N summary from ranked inputs', () => {
	const urgent = makeHoldNote({ id: 31, priority: 'urgent', expires_at: '2026-07-15T00:15:00Z' });
	const high = makeHoldNote({ id: 32, priority: 'high', expires_at: '2026-07-15T00:10:00Z' });
	const normal = makeHoldNote({ id: 33, priority: 'normal', expires_at: '2026-07-15T00:05:00Z' });

	const advisory = buildHoldAdvisory([normal, high, urgent]);
	assert.equal(advisory.primary?.id, 31);
	assert.equal(advisory.secondaryCount, 2);
	assert.equal(advisory.secondarySummary, '+2');
	assert.deepEqual(advisory.ordered.map((note) => note.id), [31, 32, 33]);

	assert.equal(formatAdditionalHoldSummary(0), '');
	assert.equal(formatAdditionalHoldSummary(1), '+1');
	assert.equal(formatAdditionalHoldSummary(3), '+3');
});
