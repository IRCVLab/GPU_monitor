import type { GpuInfo, ServerStatus } from '$lib/types';

export type CompactGpuState = 'available' | 'occupied' | 'unknown';

export const COMPACT_GPU_IDLE_UTILIZATION_MAX = 5;
export const COMPACT_GPU_IDLE_MEMORY_RATIO_MAX = 0.1;

/**
 * Compact view is availability-first. Treat a GPU as free only when the server
 * is online, telemetry is fresh enough to exist, no users are attached, and
 * the slot only shows low idle baseline activity (driver memory / small util).
 */
export function getCompactGpuState(
	serverStatus: ServerStatus,
	lastSeen: string | null,
	gpu: GpuInfo
): CompactGpuState {
	if (gpu.users.length > 0) return 'occupied';
	if (serverStatus !== 'online' || !lastSeen) return 'unknown';

	const memoryRatio =
		gpu.memory_total > 0 ? gpu.memory_used / gpu.memory_total : gpu.memory_used > 0 ? 1 : 0;

	if (
		gpu.utilization <= COMPACT_GPU_IDLE_UTILIZATION_MAX &&
		memoryRatio <= COMPACT_GPU_IDLE_MEMORY_RATIO_MAX
	) {
		return 'available';
	}

	return 'unknown';
}
