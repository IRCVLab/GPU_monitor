// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import test from 'node:test';
// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import assert from 'node:assert/strict';
// @ts-expect-error Node strip-types executes the .ts helper directly.
import { countResolvedGridTracks, placeOrderedMasonryItems } from './orderedMasonry.ts';


test('counts only positive resolved masonry grid tracks', () => {
	assert.equal(countResolvedGridTracks('352px 0px 0px'), 1);
	assert.equal(countResolvedGridTracks('352px 352px 0px'), 2);
	assert.equal(countResolvedGridTracks('352px 352px 352px'), 3);
});

test('falls back to one masonry grid track for none or invalid templates', () => {
	assert.equal(countResolvedGridTracks('none'), 1);
	assert.equal(countResolvedGridTracks(''), 1);
	assert.equal(countResolvedGridTracks('repeat(auto-fit, minmax(0, 1fr))'), 1);
});

function assertRowStartsAreMonotonic(placements: ReturnType<typeof placeOrderedMasonryItems>): void {
	for (let index = 1; index < placements.length; index += 1) {
		assert.ok(
			placements[index].gridRowStart >= placements[index - 1].gridRowStart,
			`placement ${index} starts at row ${placements[index].gridRowStart}, before previous row ${placements[index - 1].gridRowStart}`
		);
	}
}

test('places uneven DOM-order items with one-row left-biased non-decreasing row starts', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 3,
		spans: [2, 3, 1, 4, 2, 5]
	});

	assertRowStartsAreMonotonic(placements);
	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 3, 1, 3, 2]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowStart),
		[1, 1, 1, 3, 3, 4]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowEnd),
		['span 2', 'span 3', 'span 1', 'span 4', 'span 2', 'span 5']
	);
});

test('uses stable leftmost tie breaking among columns within the left-bias row window', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 3,
		spans: [2, 1, 1, 1, 1]
	});

	assertRowStartsAreMonotonic(placements);
	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 2, 3, 1]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowStart),
		[1, 1, 2, 2, 3]
	);
});

test('places the next item in the earlier column when row starts differ by one masonry row', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 3,
		spans: [5, 4, 4, 1],
		leftBiasRows: 1
	});

	assertRowStartsAreMonotonic(placements);
	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 3, 1]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowStart),
		[1, 1, 1, 6]
	);
});

test('keeps preferred columns during height-only relayout', () => {
	const previous = placeOrderedMasonryItems({ columnCount: 3, spans: [4, 1, 4, 1, 4] });
	const previousColumns = previous.map((placement) => placement.gridColumnStart);

	const changed = placeOrderedMasonryItems({
		columnCount: 3,
		spans: [4, 4, 4, 1, 4],
		preferredColumns: previousColumns
	});

	assertRowStartsAreMonotonic(changed);
	assert.deepEqual(previousColumns, [1, 2, 2, 3, 3]);
	assert.deepEqual(
		changed.map((placement) => placement.gridColumnStart),
		previousColumns
	);
});

test('ignores invalid preferred columns so structural relayout can rebalance', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 2,
		spans: [3, 3, 1, 1],
		preferredColumns: [3, null, 0, 2]
	});

	assertRowStartsAreMonotonic(placements);
	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 1, 2]
	);
});

test('left bias does not choose an earlier column when it is more than one row behind', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 3,
		spans: [6, 3, 3, 1],
		leftBiasRows: 1
	});

	assertRowStartsAreMonotonic(placements);
	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 3, 2]
	);
	assert.deepEqual(
		placements.map((placement) => placement.gridRowStart),
		[1, 1, 1, 4]
	);
});

test('drops preferred columns that no longer exist after responsive column-count shrink', () => {
	const placements = placeOrderedMasonryItems({
		columnCount: 2,
		spans: [2, 2, 2],
		preferredColumns: [1, 3, 2]
	});

	assertRowStartsAreMonotonic(placements);
	assert.deepEqual(
		placements.map((placement) => placement.gridColumnStart),
		[1, 2, 2]
	);
});
