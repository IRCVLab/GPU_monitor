export const INDICATOR_PANEL_CLEARANCE_PX = 4;

export interface IndicatorLaneHeightInput {
	compact: boolean;
	indicatorVisible: boolean;
	triggerBottom: number | null;
	panelOpen: boolean;
	panelBottom: number | null;
}

export function resolveIndicatorLaneHeight(input: IndicatorLaneHeightInput): number {
	if (!input.compact || !input.indicatorVisible) return 0;
	if (input.triggerBottom === null || !Number.isFinite(input.triggerBottom)) return 0;

	const triggerLaneHeight = Math.max(0, Math.ceil(input.triggerBottom));
	if (!input.panelOpen || input.panelBottom === null || !Number.isFinite(input.panelBottom)) {
		return triggerLaneHeight;
	}

	return Math.max(
		triggerLaneHeight,
		Math.ceil(input.panelBottom + INDICATOR_PANEL_CLEARANCE_PX)
	);
}


export function shouldSyncIndicatorLane(
	previousCompact: boolean,
	previousIndicatorVisible: boolean,
	nextCompact: boolean,
	nextIndicatorVisible: boolean
): boolean {
	return (
		previousCompact !== nextCompact ||
		previousIndicatorVisible !== nextIndicatorVisible
	);
}
