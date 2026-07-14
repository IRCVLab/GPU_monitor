// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import {
	HEADER_SCROLL_DIRECTION_THRESHOLD_PX,
	HEADER_TOP_RESET_PX,
	updateHeaderVisibility
} from './headerVisibility.ts';

test('keeps a compact header compact on subthreshold downward motion', () => {
	const result = updateHeaderVisibility({
		currentY: 18,
		previousY: 0,
		direction: null,
		accumulatedDelta: 0,
		currentCompact: true,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, true);
});

test('keeps an expanded header expanded on subthreshold upward motion', () => {
	const result = updateHeaderVisibility({
		currentY: 24,
		previousY: 42,
		direction: 'up',
		accumulatedDelta: 18,
		currentCompact: false,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, false);
});

test('top reset expands and clears accumulated motion', () => {
	const result = updateHeaderVisibility({
		currentY: HEADER_TOP_RESET_PX,
		previousY: 64,
		direction: 'up',
		accumulatedDelta: 80,
		currentCompact: true,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, false);
	assert.equal(result.indicatorVisible, false);
	assert.equal(result.nextAccumulatedDelta, 0);
});

test('compacts only after accumulated downward threshold', () => {
	const below = updateHeaderVisibility({
		currentY: HEADER_SCROLL_DIRECTION_THRESHOLD_PX - 1,
		previousY: 0,
		direction: null,
		accumulatedDelta: 0,
		currentCompact: false,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});
	const crossed = updateHeaderVisibility({
		currentY: HEADER_SCROLL_DIRECTION_THRESHOLD_PX,
		previousY: 0,
		direction: null,
		accumulatedDelta: 0,
		currentCompact: false,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(below.compact, false);
	assert.equal(crossed.compact, true);
});

test('direction change resets accumulation before expanding', () => {
	const result = updateHeaderVisibility({
		currentY: 70,
		previousY: 90,
		direction: 'down',
		accumulatedDelta: 28,
		currentCompact: true,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, true);
	assert.equal(result.nextDirection, 'up');
	assert.equal(result.nextAccumulatedDelta, 20);
});

test('desktop indicator visibility is independent of scroll position', () => {
	const result = updateHeaderVisibility({
		currentY: 24,
		previousY: 54,
		direction: 'up',
		accumulatedDelta: 30,
		currentCompact: true,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, false);
	assert.equal(result.indicatorVisible, false);
});

test('reduced motion preserves immediate threshold semantics without timers', () => {
	const result = updateHeaderVisibility({
		currentY: 60,
		previousY: 24,
		direction: 'down',
		accumulatedDelta: 0,
		currentCompact: false,
		reducedMotion: true,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, true);
	assert.equal(result.nextPreviousY, 60);
});
