import type { GpuInfo, ServerState } from '../types';

export const COMPACT_GPU_BANK_SIZE = 8;

export function compactGpuBankCount(servers: Pick<ServerState, 'gpus'>[]): number {
	const maxIndex = Math.max(
		-1,
		...servers.flatMap((server) => server.gpus.map((gpu) => gpu.index))
	);
	return Math.max(1, Math.floor(maxIndex / COMPACT_GPU_BANK_SIZE) + 1);
}

export function compactGpuBankSlots(gpus: GpuInfo[], bankIndex: number): Array<GpuInfo | null> {
	const start = bankIndex * COMPACT_GPU_BANK_SIZE;
	const byIndex = new Map(gpus.map((gpu) => [gpu.index, gpu]));
	return Array.from({ length: COMPACT_GPU_BANK_SIZE }, (_, offset) =>
		byIndex.get(start + offset) ?? null
	);
}
