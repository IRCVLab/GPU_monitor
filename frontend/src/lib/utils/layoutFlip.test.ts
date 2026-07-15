// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { flipDelta, shouldAnimateFlip } from './layoutFlip.ts';

test('flipDelta returns the inverse document-space translation', () => {
	assert.deepEqual(
		flipDelta({ left: 120, top: 300 }, { left: 360, top: 180 }),
		{ x: -240, y: 120 }
	);
});

test('shouldAnimateFlip ignores zero movement and reduced motion', () => {
	assert.equal(shouldAnimateFlip({ x: 0, y: 0 }, false), false);
	assert.equal(shouldAnimateFlip({ x: 4, y: 0 }, true), false);
	assert.equal(shouldAnimateFlip({ x: 4, y: -8 }, false), true);
});

