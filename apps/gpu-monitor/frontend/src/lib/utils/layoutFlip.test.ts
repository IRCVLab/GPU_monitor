// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { animateFlip, flipDelta, shouldAnimateFlip } from './layoutFlip.ts';

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

test('shouldAnimateFlip allows vertical-only movement with stable x', () => {
	assert.equal(shouldAnimateFlip({ x: 0, y: 12 }, false), true);
});

test('animateFlip uses the tuned non-jumping transition contract', () => {
	const calls = [];
	const element = {
		animate: (...args) => {
			calls.push(args);
			return { addEventListener() {} };
		}
	};

	animateFlip(element, { left: 10, top: 10 }, { left: 10, top: 40 }, false);

	assert.deepEqual(calls[0][0], [
		{ transform: 'translate3d(0px, -30px, 0)' },
		{ transform: 'translate3d(0, 0, 0)' }
	]);
	assert.equal(calls[0][1].duration, 400);
	assert.equal(calls[0][1].easing, 'cubic-bezier(0.22, 1, 0.36, 1)');
	assert.equal(calls[0][1].fill, 'both');
});

