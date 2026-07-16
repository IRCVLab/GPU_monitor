export type OrderedMasonryPlacement = {
	gridColumnStart: number;
	gridRowStart: number;
	gridRowEnd: string;
};

export type OrderedMasonryInput = {
	columnCount: number;
	spans: readonly number[];
};

export function placeOrderedMasonryItems({
	columnCount,
	spans
}: OrderedMasonryInput): OrderedMasonryPlacement[] {
	const columns = Math.max(1, Math.floor(columnCount));
	const nextRows = Array.from({ length: columns }, () => 1);

	let previousGridRowStart = 1;

	return spans.map((spanValue) => {
		let columnIndex = 0;
		for (let index = 1; index < nextRows.length; index += 1) {
			if (nextRows[index] < nextRows[columnIndex]) columnIndex = index;
		}

		const span = Math.max(1, Math.ceil(spanValue));
		const gridRowStart = Math.max(nextRows[columnIndex], previousGridRowStart);
		nextRows[columnIndex] = gridRowStart + span;
		previousGridRowStart = gridRowStart;

		return {
			gridColumnStart: columnIndex + 1,
			gridRowStart,
			gridRowEnd: `span ${span}`
		};
	});
}
