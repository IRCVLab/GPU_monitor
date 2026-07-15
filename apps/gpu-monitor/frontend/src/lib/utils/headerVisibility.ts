export const HEADER_SCROLL_DIRECTION_THRESHOLD_PX = 30;
export const HEADER_TOP_RESET_PX = 12;
export const HEADER_INDICATOR_TOP_MIN_PX = 12;
export const HEADER_INDICATOR_TOP_MAX_PX = 16;

export type HeaderScrollDirection = 'up' | 'down' | null;

export interface HeaderVisibilityInput {
	currentY: number;
	previousY: number;
	direction: HeaderScrollDirection;
	accumulatedDelta: number;
	currentCompact: boolean;
	reducedMotion: boolean;
	viewportWidth: number;
}

export interface HeaderVisibilityResult {
	compact: boolean;
	indicatorVisible: boolean;
	nextPreviousY: number;
	nextDirection: HeaderScrollDirection;
	nextAccumulatedDelta: number;
}

export function compensateHeaderScrollPosition(
	scrollY: number,
	expandedHeight: number,
	renderedHeight: number
): number {
	return Math.max(0, scrollY + Math.max(0, expandedHeight - renderedHeight));
}

export function shouldRevealSettledHeaderAtTop(
	scrollY: number,
	currentCompact: boolean,
	renderedHeight: number
): boolean {
	return currentCompact && scrollY <= HEADER_TOP_RESET_PX && renderedHeight <= 1.5;
}

export function updateHeaderVisibility(input: HeaderVisibilityInput): HeaderVisibilityResult {
	const delta = input.currentY - input.previousY;
	const nextDirection: HeaderScrollDirection = delta > 0 ? 'down' : delta < 0 ? 'up' : input.direction;
	const directionChanged = input.direction !== null && nextDirection !== input.direction;
	const nextAccumulatedDelta = input.currentY <= HEADER_TOP_RESET_PX
		? 0
		: directionChanged
			? Math.abs(delta)
			: input.accumulatedDelta + Math.abs(delta);

	if (input.currentY <= HEADER_TOP_RESET_PX) {
		return {
			compact: false,
			indicatorVisible: false,
			nextPreviousY: input.currentY,
			nextDirection,
			nextAccumulatedDelta: 0
		};
	}

	const crossedThreshold = nextAccumulatedDelta >= HEADER_SCROLL_DIRECTION_THRESHOLD_PX;
	const compact = crossedThreshold ? nextDirection === 'down' : input.currentCompact;

	return {
		compact,
		indicatorVisible: compact && input.viewportWidth >= 921,
		nextPreviousY: input.currentY,
		nextDirection,
		nextAccumulatedDelta
	};
}
