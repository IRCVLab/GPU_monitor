// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';

import { isTelemetryStale } from './telemetryFreshness.ts';

test('isTelemetryStale uses an actual age threshold', () => {
	const nowMs = Date.parse('2026-07-15T00:00:00Z');
	assert.equal(isTelemetryStale('2026-07-14T23:58:50Z', nowMs, 60_000), true);
	assert.equal(isTelemetryStale('2026-07-14T23:59:20Z', nowMs, 60_000), false);
	assert.equal(isTelemetryStale(null, nowMs, 60_000), true);
});
