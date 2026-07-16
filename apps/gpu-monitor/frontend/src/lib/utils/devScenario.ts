import type { GpuInfo, GpuInventory, ServerState, StatusReason, SystemInfo } from '$lib/types';

export const DEV_SCENARIOS = ['normal', 'stale', 'io', 'offline', 'gpu_missing', 'mixed'] as const;
export type DevScenario = (typeof DEV_SCENARIOS)[number];

const STALE_AGE_MS = 90_000;
const OFFLINE_AGE_MS = 5 * 60_000;
const IO_AGE_MS = 15_000;

export function isDevScenario(value: string | null | undefined): value is DevScenario {
	return DEV_SCENARIOS.includes(value as DevScenario);
}

function isoAt(nowMs: number, ageMs: number): string {
	return new Date(nowMs - ageMs).toISOString();
}

function createReason(code: string, message: string, nowMs: number): StatusReason {
	return {
		code,
		source: 'dev-scenario',
		message,
		retryable: true,
		updated_at: new Date(nowMs).toISOString()
	};
}

function cloneSystem(system: ServerState['system']): SystemInfo {
	return {
		cpu_percent: system?.cpu_percent ?? 0,
		ram_used: system?.ram_used ?? 0,
		ram_total: system?.ram_total ?? 0,
		io_pressure_some: system?.io_pressure_some ?? null,
		io_pressure_full: system?.io_pressure_full ?? null,
		io_blocked_tasks: system?.io_blocked_tasks ?? null,
		io_pressure_supported: system?.io_pressure_supported === true
	};
}

function applyStaleState(state: ServerState, nowMs: number): ServerState {
	return {
		...state,
		status: 'online',
		last_seen: isoAt(nowMs, STALE_AGE_MS),
		status_reason: createReason(
			'stale_snapshot',
			'SIMULATION · telemetry is stale by about 90 seconds',
			nowMs
		)
	};
}

function applyIoState(state: ServerState, nowMs: number): ServerState {
	return {
		...state,
		status: 'online',
		last_seen: state.last_seen ?? isoAt(nowMs, IO_AGE_MS),
		system: {
			...cloneSystem(state.system),
			io_pressure_some: 38.4,
			io_pressure_full: 12.8,
			io_blocked_tasks: 4,
			io_pressure_supported: true
		},
		status_reason: createReason(
			'io_pressure',
			'SIMULATION · I/O bottleneck pressure is elevated',
			nowMs
		)
	};
}

function applyOfflineState(state: ServerState, nowMs: number): ServerState {
	return {
		...state,
		status: 'offline',
		last_seen: isoAt(nowMs, OFFLINE_AGE_MS),
		status_reason: createReason(
			'timeout',
			'SIMULATION · SSH timeout while collecting telemetry',
			nowMs
		)
	};
}

function cloneGpu(gpu: GpuInfo): GpuInfo {
	return {
		index: gpu.index,
		name: gpu.name,
		utilization: gpu.utilization,
		memory_used: gpu.memory_used,
		memory_total: gpu.memory_total,
		temperature: gpu.temperature,
		power_draw: gpu.power_draw,
		users: [...gpu.users]
	};
}

function buildMissingInventory(state: ServerState, visibleGpus: GpuInfo[], missingIndex: number | null): GpuInventory {
	const current = state.gpu_inventory;
	const expectedCount = Math.max(current?.expected_count ?? state.gpus.length, visibleGpus.length + 1);
	const previousMissingIndices = current?.missing_indices ?? [];
	const missingIndices =
		missingIndex !== null
			? [missingIndex]
			: previousMissingIndices.length > 0
				? previousMissingIndices
				: [visibleGpus.length];

	return {
		state: 'missing',
		visible_count: visibleGpus.length,
		expected_count: expectedCount,
		pci_count: Math.max(current?.pci_count ?? expectedCount, expectedCount),
		missing_indices: missingIndices
	};
}

function applyGpuMissingState(state: ServerState, nowMs: number): ServerState {
	const visibleGpus = state.gpus.map(cloneGpu);
	const removed = visibleGpus.length > 0 ? visibleGpus.pop() ?? null : null;
	const missingIndex = removed?.index ?? state.gpu_inventory?.missing_indices?.[0] ?? null;
	const degradedGpus = removed ? visibleGpus : state.gpus.map(cloneGpu);

	return {
		...state,
		status: 'degraded',
		gpus: degradedGpus,
		gpu_inventory: buildMissingInventory(state, degradedGpus, missingIndex),
		status_reason: createReason(
			'gpu_device_missing',
			'SIMULATION · GPU inventory mismatch detected (missing GPU device)',
			nowMs
		)
	};
}

function replaceAt(
	states: readonly ServerState[],
	index: number,
	apply: (state: ServerState, nowMs: number) => ServerState,
	nowMs: number
): ServerState[] {
	if (index < 0 || index >= states.length) return [...states];
	return states.map((state, currentIndex) =>
		currentIndex === index ? apply(state, nowMs) : state
	);
}

export function applyDevScenario(
	states: readonly ServerState[],
	scenario: DevScenario,
	nowMs = Date.now()
): ServerState[] {
	if (scenario === 'normal' || states.length === 0) return states as ServerState[];
	if (scenario === 'stale') return replaceAt(states, 0, applyStaleState, nowMs);
	if (scenario === 'io') return replaceAt(states, 0, applyIoState, nowMs);
	if (scenario === 'offline') return replaceAt(states, 0, applyOfflineState, nowMs);
	if (scenario === 'gpu_missing') return replaceAt(states, 0, applyGpuMissingState, nowMs);

	let next = replaceAt(states, 0, applyStaleState, nowMs);
	next = replaceAt(next, 1, applyIoState, nowMs);
	next = replaceAt(next, 2, applyOfflineState, nowMs);
	next = replaceAt(next, 3, applyGpuMissingState, nowMs);
	return next;
}
