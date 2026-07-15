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

	return spans.map((spanValue, index) => {
		const columnIndex = index % columns;
		const span = Math.max(1, Math.ceil(spanValue));
		const gridRowStart = nextRows[columnIndex];
		nextRows[columnIndex] += span;

		return {
			gridColumnStart: columnIndex + 1,
			gridRowStart,
			gridRowEnd: `span ${span}`
		};
	});
}
