// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import test from 'node:test';
// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import assert from 'node:assert/strict';
// @ts-expect-error Node strip-types executes the .ts helper directly.
import { placeOrderedMasonryItems } from './orderedMasonry.ts';

test('places DOM-order items into stable round-robin columns with independent row starts', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 3,
		spans: [2, 3, 1, 4, 2, 5]
	});

	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 3, 1, 2, 3]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowStart),
		[1, 1, 1, 3, 4, 2]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowEnd),
		['span 2', 'span 3', 'span 1', 'span 4', 'span 2', 'span 5']
	);
});
