// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import test from 'node:test';
// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import assert from 'node:assert/strict';
// @ts-expect-error Node strip-types executes the .ts helper directly.
import { placeOrderedMasonryItems } from './orderedMasonry.ts';

function assertRowStartsAreMonotonic(placements: ReturnType<typeof placeOrderedMasonryItems>): void {
	for (let index = 1; index < placements.length; index += 1) {
		assert.ok(
			placements[index].gridRowStart >= placements[index - 1].gridRowStart,
			`placement ${index} starts at row ${placements[index].gridRowStart}, before previous row ${placements[index - 1].gridRowStart}`
		);
	}
}

test('places uneven DOM-order items with non-decreasing row starts', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 3,
		spans: [2, 3, 1, 4, 2, 5]
	});

	assertRowStartsAreMonotonic(placements);
	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 3, 3, 1, 2]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowStart),
		[1, 1, 1, 2, 3, 4]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowEnd),
		['span 2', 'span 3', 'span 1', 'span 4', 'span 2', 'span 5']
	);
});

test('uses stable leftmost tie breaking for the currently shortest column', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 3,
		spans: [2, 1, 1, 1, 1]
	});

	assertRowStartsAreMonotonic(placements);
	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 3, 2, 3]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowStart),
		[1, 1, 1, 2, 2]
	);
});
