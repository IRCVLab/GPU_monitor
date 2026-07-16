import { writable, derived } from 'svelte/store';
import type { Writable, Readable } from 'svelte/store';
import type { ServerState } from '$lib/types';

export const serverStates: Writable<Map<number, ServerState>> = writable(new Map());

const VALID_STATUS = new Set(['online', 'offline', 'degraded', 'unknown']);
const VALID_NETWORK = new Set(['internal', 'external']);

function toFiniteNumber(value: unknown, fallback = 0): number {
	return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function toOptionalFiniteNumber(value: unknown): number | undefined {
	return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function toNullableFiniteNumber(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function toStringArray(value: unknown): string[] {
	return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function sameStringArray(a: string[], b: string[]): boolean {
	if (a.length !== b.length) return false;
	for (let index = 0; index < a.length; index += 1) {
		if (a[index] !== b[index]) return false;
	}
	return true;
}

function toNonNegativeIntegerArray(value: unknown): number[] {
	return Array.isArray(value)
		? value.filter((item): item is number => Number.isInteger(item) && item >= 0)
		: [];
}

function sameNumberArray(a: number[], b: number[]): boolean {
	if (a.length !== b.length) return false;
	for (let index = 0; index < a.length; index += 1) {
		if (a[index] !== b[index]) return false;
	}
	return true;
}

function sameGpuInfo(a: ServerState['gpus'][number], b: ServerState['gpus'][number]): boolean {
	return (
		a.index === b.index &&
		a.name === b.name &&
		a.utilization === b.utilization &&
		a.memory_used === b.memory_used &&
		a.memory_total === b.memory_total &&
		a.temperature === b.temperature &&
		a.power_draw === b.power_draw &&
		sameStringArray(a.users, b.users)
	);
}

function sameSystemInfo(a: ServerState['system'], b: ServerState['system']): boolean {
	if (a === b) return true;
	if (!a || !b) return a === b;
	return (
		a.cpu_percent === b.cpu_percent &&
		a.ram_used === b.ram_used &&
		a.ram_total === b.ram_total &&
		a.cpu_pressure_some === b.cpu_pressure_some &&
		a.cpu_running_tasks === b.cpu_running_tasks &&
		a.load_avg_1 === b.load_avg_1 &&
		a.load_avg_5 === b.load_avg_5 &&
		a.load_avg_15 === b.load_avg_15 &&
		a.cpu_count === b.cpu_count &&
		a.io_pressure_some === b.io_pressure_some &&
		a.io_pressure_full === b.io_pressure_full &&
		a.io_blocked_tasks === b.io_blocked_tasks &&
		a.io_pressure_supported === b.io_pressure_supported &&
		a.disk_read_bytes_per_second === b.disk_read_bytes_per_second &&
		a.disk_write_bytes_per_second === b.disk_write_bytes_per_second &&
		a.disk_sample_seconds === b.disk_sample_seconds
	);
}

function sameStorageMount(a: NonNullable<ServerState['storage']>['mounts'][number], b: NonNullable<ServerState['storage']>['mounts'][number]): boolean {
	return (
		a.mount === b.mount &&
		a.device === b.device &&
		a.fs_type === b.fs_type &&
		a.size === b.size &&
		a.used === b.used &&
		a.available === b.available &&
		a.percent === b.percent
	);
}

function sameStorageInfo(a: ServerState['storage'], b: ServerState['storage']): boolean {
	if (a === b) return true;
	if (!a || !b) return a === b;
	if (
		a.collected_at !== b.collected_at ||
		a.summary.mount_count !== b.summary.mount_count ||
		a.summary.total !== b.summary.total ||
		a.summary.used !== b.summary.used ||
		a.summary.percent !== b.summary.percent ||
		a.mounts.length !== b.mounts.length
	) {
		return false;
	}

	for (let index = 0; index < a.mounts.length; index += 1) {
		if (!sameStorageMount(a.mounts[index], b.mounts[index])) return false;
	}

	return true;
}

function sameStatusReason(a: ServerState['status_reason'], b: ServerState['status_reason']): boolean {
	if (a === b) return true;
	if (!a || !b) return a === b;
	return (
		a.code === b.code &&
		a.source === b.source &&
		a.message === b.message &&
		a.retryable === b.retryable &&
		a.updated_at === b.updated_at
	);
}

function sameGpuInventory(a: ServerState['gpu_inventory'], b: ServerState['gpu_inventory']): boolean {
	if (a === b) return true;
	if (!a || !b) return a === b;
	return (
		a.state === b.state &&
		a.visible_count === b.visible_count &&
		a.expected_count === b.expected_count &&
		a.pci_count === b.pci_count &&
		sameNumberArray(a.missing_indices, b.missing_indices)
	);
}

export function isServerStateEqual(a: ServerState, b: ServerState): boolean {
	if (
		a.server_id !== b.server_id ||
		a.server_name !== b.server_name ||
		a.host !== b.host ||
		a.port !== b.port ||
		a.network !== b.network ||
		a.status !== b.status ||
		!sameStatusReason(a.status_reason, b.status_reason) ||
		a.last_seen !== b.last_seen ||
		a.display_order !== b.display_order ||
		!sameSystemInfo(a.system, b.system) ||
		!sameStorageInfo(a.storage, b.storage) ||
		!sameGpuInventory(a.gpu_inventory, b.gpu_inventory) ||
		a.gpus.length !== b.gpus.length
	) {
		return false;
	}

	for (let index = 0; index < a.gpus.length; index += 1) {
		if (!sameGpuInfo(a.gpus[index], b.gpus[index])) return false;
	}

	return true;
}

export function normalizeServerState(value: unknown, fallbackId?: number): ServerState | null {
	if (!value || typeof value !== 'object') return null;

	const raw = value as Record<string, unknown>;
	const serverId =
		typeof raw.server_id === 'number' && Number.isFinite(raw.server_id)
			? raw.server_id
			: fallbackId;

	if (typeof serverId !== 'number' || !Number.isFinite(serverId)) return null;

	const rawGpus = Array.isArray(raw.gpus) ? raw.gpus : [];
	const gpus = rawGpus
		.map((gpu) => {
			if (!gpu || typeof gpu !== 'object') return null;
			const item = gpu as Record<string, unknown>;
			return {
				index: toFiniteNumber(item.index),
				name: typeof item.name === 'string' ? item.name : `GPU ${toFiniteNumber(item.index)}`,
				utilization: toFiniteNumber(item.utilization),
				memory_used: toFiniteNumber(item.memory_used),
				memory_total: toFiniteNumber(item.memory_total),
				temperature: toFiniteNumber(item.temperature),
				power_draw: toFiniteNumber(item.power_draw),
				users: toStringArray(item.users)
			};
		})
		.filter((gpu): gpu is ServerState['gpus'][number] => gpu !== null);

	let system: ServerState['system'] = null;
	if (raw.system && typeof raw.system === 'object') {
		const rawSystem = raw.system as Record<string, unknown>;
		system = {
			cpu_percent: toFiniteNumber(rawSystem.cpu_percent),
			ram_used: toFiniteNumber(rawSystem.ram_used),
			ram_total: toFiniteNumber(rawSystem.ram_total),
			cpu_pressure_some: toNullableFiniteNumber(rawSystem.cpu_pressure_some),
			cpu_running_tasks: toNullableFiniteNumber(rawSystem.cpu_running_tasks),
			load_avg_1: toNullableFiniteNumber(rawSystem.load_avg_1),
			load_avg_5: toNullableFiniteNumber(rawSystem.load_avg_5),
			load_avg_15: toNullableFiniteNumber(rawSystem.load_avg_15),
			cpu_count: toNullableFiniteNumber(rawSystem.cpu_count),
			io_pressure_some: toNullableFiniteNumber(rawSystem.io_pressure_some),
			io_pressure_full: toNullableFiniteNumber(rawSystem.io_pressure_full),
			io_blocked_tasks: toNullableFiniteNumber(rawSystem.io_blocked_tasks),
			io_pressure_supported: rawSystem.io_pressure_supported === true,
			disk_read_bytes_per_second: toOptionalFiniteNumber(rawSystem.disk_read_bytes_per_second),
			disk_write_bytes_per_second: toOptionalFiniteNumber(rawSystem.disk_write_bytes_per_second),
			disk_sample_seconds: toOptionalFiniteNumber(rawSystem.disk_sample_seconds)
		};
	}

	let storage: ServerState['storage'] = null;
	if (raw.storage && typeof raw.storage === 'object') {
		const rawStorage = raw.storage as Record<string, unknown>;
		const rawSummary =
			rawStorage.summary && typeof rawStorage.summary === 'object'
				? (rawStorage.summary as Record<string, unknown>)
				: {};
		const rawMounts = Array.isArray(rawStorage.mounts) ? rawStorage.mounts : [];
		storage = {
			collected_at: typeof rawStorage.collected_at === 'string' ? rawStorage.collected_at : null,
			summary: {
				mount_count: toFiniteNumber(rawSummary.mount_count),
				total: toFiniteNumber(rawSummary.total),
				used: toFiniteNumber(rawSummary.used),
				percent: toFiniteNumber(rawSummary.percent)
			},
			mounts: rawMounts
				.map((mount) => {
					if (!mount || typeof mount !== 'object') return null;
					const item = mount as Record<string, unknown>;
					return {
						mount: typeof item.mount === 'string' ? item.mount : '',
						device: typeof item.device === 'string' ? item.device : '',
						fs_type: typeof item.fs_type === 'string' ? item.fs_type : '',
						size: toFiniteNumber(item.size),
						used: toFiniteNumber(item.used),
						available: toFiniteNumber(item.available),
						percent: toFiniteNumber(item.percent)
					};
				})
				.filter((mount): mount is NonNullable<ServerState['storage']>['mounts'][number] => mount !== null)
		};
	}

	let gpuInventory: ServerState['gpu_inventory'];
	if (raw.gpu_inventory && typeof raw.gpu_inventory === 'object') {
		const rawInventory = raw.gpu_inventory as Record<string, unknown>;
		gpuInventory = {
			state: typeof rawInventory.state === 'string' ? rawInventory.state : 'unknown',
			visible_count: toFiniteNumber(rawInventory.visible_count),
			expected_count: toFiniteNumber(rawInventory.expected_count),
			pci_count: toFiniteNumber(rawInventory.pci_count),
			missing_indices: toNonNegativeIntegerArray(rawInventory.missing_indices)
		};
	}

	let statusReason: ServerState['status_reason'] = null;
	if (raw.status_reason && typeof raw.status_reason === 'object') {
		const rawReason = raw.status_reason as Record<string, unknown>;
		statusReason = {
			code: typeof rawReason.code === 'string' ? rawReason.code : 'unknown',
			source: typeof rawReason.source === 'string' ? rawReason.source : 'collector',
			message: typeof rawReason.message === 'string' ? rawReason.message : '',
			retryable: typeof rawReason.retryable === 'boolean' ? rawReason.retryable : true,
			updated_at: typeof rawReason.updated_at === 'string' ? rawReason.updated_at : null
		};
	}

	return {
		server_id: serverId,
		server_name: typeof raw.server_name === 'string' ? raw.server_name : `Server ${serverId}`,
		host: typeof raw.host === 'string' ? raw.host : '',
		port: toOptionalFiniteNumber(raw.port),
		network: VALID_NETWORK.has(raw.network as string)
			? (raw.network as ServerState['network'])
			: 'internal',
		status: VALID_STATUS.has(raw.status as string)
			? (raw.status as ServerState['status'])
			: 'unknown',
		status_reason: statusReason,
		last_seen: typeof raw.last_seen === 'string' ? raw.last_seen : null,
		gpus,
		system,
		storage,
		gpu_inventory: gpuInventory,
		display_order: toOptionalFiniteNumber(raw.display_order)
	};
}

export const internalServers: Readable<ServerState[]> = derived(serverStates, ($map) =>
	[...$map.values()]
		.filter((s) => s.network === 'internal')
		.sort((a, b) => (a.display_order ?? a.server_id) - (b.display_order ?? b.server_id))
);

export const externalServers: Readable<ServerState[]> = derived(serverStates, ($map) =>
	[...$map.values()]
		.filter((s) => s.network === 'external')
		.sort((a, b) => (a.display_order ?? a.server_id) - (b.display_order ?? b.server_id))
);
