export function countResolvedGridTracks(gridTemplateColumns: string): number {
	const tokens = gridTemplateColumns.trim().split(/\s+/).filter(Boolean);
	const positiveTrackCount = tokens.filter((token) => {
		if (!token.endsWith('px')) return false;
		const value = Number.parseFloat(token);
		return Number.isFinite(value) && value > 0;
	}).length;
	return Math.max(1, positiveTrackCount);
}

export type OrderedMasonryPlacement = {
	gridColumnStart: number;
	gridRowStart: number;
	gridRowEnd: string;
};

export type OrderedMasonryInput = {
	columnCount: number;
	spans: readonly number[];
	preferredColumns?: readonly (number | null)[];
	leftBiasRows?: number;
};

export function placeOrderedMasonryItems({
	columnCount,
	spans,
	preferredColumns,
	leftBiasRows = 1
}: OrderedMasonryInput): OrderedMasonryPlacement[] {
	const columns = Math.max(1, Math.floor(columnCount));
	const biasRows = Math.max(0, Math.floor(leftBiasRows));
	const nextRows = Array.from({ length: columns }, () => 1);

	let previousGridRowStart = 1;

	return spans.map((spanValue, itemIndex) => {
		const preferredColumn = preferredColumns?.[itemIndex] ?? null;
		const preferredColumnIndex =
			typeof preferredColumn === 'number' && Number.isInteger(preferredColumn) && preferredColumn >= 1 && preferredColumn <= columns
				? preferredColumn - 1
				: null;
		let columnIndex: number;

		if (preferredColumnIndex !== null) {
			columnIndex = preferredColumnIndex;
		} else {
			const minimum = Math.min(...nextRows);
			const eligible = nextRows
				.map((row, index) => ({ row, index }))
				.filter(({ row }) => row <= minimum + biasRows);
			columnIndex = eligible[0].index;
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
