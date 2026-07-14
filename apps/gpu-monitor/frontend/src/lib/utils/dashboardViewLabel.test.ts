// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { dashboardViewLabel } from './dashboardViewLabel.ts';

test('maps stored dashboard values to visible labels', () => {
	assert.equal(dashboardViewLabel('default'), 'Full');
	assert.equal(dashboardViewLabel('compact'), 'Compact');
});
