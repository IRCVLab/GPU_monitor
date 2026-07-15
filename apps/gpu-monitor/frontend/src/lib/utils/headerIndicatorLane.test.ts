// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import test from 'node:test';
// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import assert from 'node:assert/strict';
import {
	INDICATOR_PANEL_CLEARANCE_PX,
	resolveIndicatorLaneHeight,
	shouldSyncIndicatorLane
// @ts-expect-error Node strip-types executes the .ts helper directly.
} from './headerIndicatorLane.ts';

test('compact closed state reserves only the slim trigger lane', () => {
	assert.equal(
		resolveIndicatorLaneHeight({
			compact: true,
			indicatorVisible: true,
			triggerBottom: 36,
			panelOpen: false,
			panelBottom: null
		}),
		36
	);
});

test('open compact panel expands lane to the measured panel bottom plus clearance', () => {
	assert.equal(
		resolveIndicatorLaneHeight({
			compact: true,
			indicatorVisible: true,
			triggerBottom: 36,
			panelOpen: true,
			panelBottom: 148.2
		}),
		Math.ceil(148.2 + INDICATOR_PANEL_CLEARANCE_PX)
	);
});

test('expanded header or hidden indicator does not reserve a compact lane', () => {
	assert.equal(
		resolveIndicatorLaneHeight({
			compact: false,
			indicatorVisible: false,
			triggerBottom: 36,
			panelOpen: false,
			panelBottom: null
		}),
		0
	);
	assert.equal(
		resolveIndicatorLaneHeight({
			compact: true,
			indicatorVisible: false,
			triggerBottom: 36,
			panelOpen: true,
			panelBottom: 148
		}),
		0
	);
});


test("lane sync runs only when compact visibility state actually changes", () => {
	assert.equal(shouldSyncIndicatorLane(false, false, true, true), true);
	assert.equal(shouldSyncIndicatorLane(true, true, false, false), true);
	assert.equal(shouldSyncIndicatorLane(true, false, true, true), true);
	assert.equal(shouldSyncIndicatorLane(true, true, true, true), false);
	assert.equal(shouldSyncIndicatorLane(false, false, false, false), false);
});
