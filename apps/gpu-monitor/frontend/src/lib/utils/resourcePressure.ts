export type PressureLevel = 'unknown' | 'idle' | 'pressure' | 'bottleneck';

export function classifyPressure(avg10: number | null | undefined): PressureLevel {
	if (typeof avg10 !== 'number' || !Number.isFinite(avg10)) return 'unknown';
	if (avg10 >= 20) return 'bottleneck';
	if (avg10 >= 5) return 'pressure';
	return 'idle';
}

export function pressureLabel(level: PressureLevel): '–' | '여유' | '압박' | '병목' {
	switch (level) {
		case 'idle':
			return '여유';
		case 'pressure':
			return '압박';
		case 'bottleneck':
			return '병목';
		default:
			return '–';
	}
}

export function normalizeLoadRatio(
	loadAvg1: number | null | undefined,
	cpuCount: number | null | undefined
): number | null {
	if (typeof loadAvg1 !== 'number' || !Number.isFinite(loadAvg1) || loadAvg1 < 0) return null;
	if (typeof cpuCount !== 'number' || !Number.isFinite(cpuCount) || cpuCount < 0) return null;
	return loadAvg1 / Math.max(cpuCount, 1);
}

export function classifyLoadRatio(ratio: number | null | undefined): PressureLevel {
	if (typeof ratio !== 'number' || !Number.isFinite(ratio) || ratio < 0) return 'unknown';
	if (ratio >= 1) return 'bottleneck';
	if (ratio >= 0.7) return 'pressure';
	return 'idle';
}
