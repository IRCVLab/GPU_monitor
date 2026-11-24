// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { mergePolledServerStates, mergeServerRecordState, selectPolledServerState } from './serverStateMerge.ts';
import { isServerStateEqual, normalizeServerState } from '../stores/servers.ts';

function record(id, name, displayOrder) {
	return {
		id,
		name,
		host: `${name.toLowerCase()}.internal`,
		port: 2200 + id,
		ssh_user: 'ircv',
		has_password: false,
		has_key: true,
		network: 'internal',
		display_order: displayOrder,
		registered_by: null,
		created_at: '2026-07-15T00:00:00Z'
	};
}

function status(id, name) {
	return {
		server_id: id,
		server_name: `stale-${name}`,
		host: 'stale-host',
		port: 9999,
		network: 'external',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: null,
		storage: null
	};
}

test('polling preserves a server updated or removed while the request was pending', () => {
	const before = status(1, 'Atlas');
	const realtime = { ...before, status: 'offline', last_seen: before.last_seen };
	assert.equal(selectPolledServerState(before, realtime, before), realtime);
	assert.equal(selectPolledServerState(before, undefined, before), null);
	assert.equal(selectPolledServerState(undefined, realtime, before), realtime);
});

test('polling applies a new sample and rejects a sample older than the current one', () => {
	const earlier = status(1, 'Atlas');
	const later = { ...earlier, last_seen: '2026-07-15T00:00:20Z' };
	assert.equal(selectPolledServerState(earlier, earlier, later), later);
	assert.equal(selectPolledServerState(later, later, earlier), later);
});

test('an authoritative poll removes missing servers but preserves changes received while polling', () => {
	const removed = status(1, 'Atlas');
	const unchanged = status(2, 'Orion');
	const changed = { ...unchanged, status: 'offline' };
	const added = status(3, 'Zeus');
	const before = new Map([[1, removed], [2, unchanged]]);
	const current = new Map([[1, removed], [2, changed], [3, added]]);
	const incoming = new Map([[2, unchanged]]);
	assert.deepEqual([...mergePolledServerStates(before, current, incoming, true)], [[2, changed], [3, added]]);
	assert.deepEqual([...mergePolledServerStates(before, current, incoming, false)], [...current]);
});

test('catalog display order survives status snapshots that omit display_order', () => {
	const later = mergeServerRecordState(record(1, 'Later', 20), status(1, 'Later'));
	const earlier = mergeServerRecordState(record(9, 'Earlier', 10), status(9, 'Earlier'));
	const ordered = [later, earlier].sort(
		(a, b) => (a.display_order ?? a.server_id) - (b.display_order ?? b.server_id)
	);

	assert.deepEqual(ordered.map((server) => server.server_name), ['Earlier', 'Later']);
	assert.deepEqual(ordered.map((server) => server.display_order), [10, 20]);
});

test('catalog identity stays authoritative while normalized telemetry is retained', () => {
	const serverRecord = record(7, 'Poseidon', 3);
	const merged = mergeServerRecordState(serverRecord, {
		...status(7, 'Poseidon'),
		display_order: 99,
		gpus: [{ index: 0, name: 'H100', utilization: 82, memory_used: 10, memory_total: 80, temperature: 60, power_draw: 250, users: ['alice'] }]
	});

	assert.equal(merged.server_name, 'Poseidon');
	assert.equal(merged.host, 'poseidon.internal');
	assert.equal(merged.port, 2207);
	assert.equal(merged.network, 'internal');
	assert.equal(merged.display_order, 3);
	assert.equal(merged.status, 'online');
	assert.equal(merged.gpus[0].users[0], 'alice');
});

test('normalizeServerState preserves PSI telemetry fields on system snapshots', () => {
	const normalized = normalizeServerState({
		server_id: 11,
		server_name: 'Atlas',
		host: 'atlas.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 41,
			ram_used: 64000,
			ram_total: 128000,
			io_pressure_some: 0.27,
			io_pressure_full: 0.04,
			io_blocked_tasks: 3,
			io_pressure_supported: true
		},
		storage: null
	});

	assert.equal(normalized?.system?.io_pressure_some, 0.27);
	assert.equal(normalized?.system?.io_pressure_full, 0.04);
	assert.equal(normalized?.system?.io_blocked_tasks, 3);
	assert.equal(normalized?.system?.io_pressure_supported, true);
});

test('isServerStateEqual treats PSI changes as meaningful telemetry updates', () => {
	const baseline = normalizeServerState({
		server_id: 11,
		server_name: 'Atlas',
		host: 'atlas.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 41,
			ram_used: 64000,
			ram_total: 128000,
			io_pressure_some: 0.27,
			io_pressure_full: 0.04,
			io_blocked_tasks: 3,
			io_pressure_supported: true
		},
		storage: null
	});

	const changedPsi = normalizeServerState({
		server_id: 11,
		server_name: 'Atlas',
		host: 'atlas.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 41,
			ram_used: 64000,
			ram_total: 128000,
			io_pressure_some: 0.51,
			io_pressure_full: 0.04,
			io_blocked_tasks: 3,
			io_pressure_supported: true
		},
		storage: null
	});

	assert.equal(isServerStateEqual(baseline, changedPsi), false);
});


test('normalizeServerState preserves optional disk throughput and sample interval fields', () => {
	const normalized = normalizeServerState({
		server_id: 21,
		server_name: 'IoBox',
		host: 'iobox.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 12,
			ram_used: 1024,
			ram_total: 2048,
			io_pressure_some: 0,
			io_pressure_full: 0,
			io_blocked_tasks: 0,
			io_pressure_supported: true,
			disk_read_bytes_per_second: 1048576,
			disk_write_bytes_per_second: 2097152,
			disk_sample_seconds: 10
		},
		storage: null
	});

	assert.equal(normalized?.system?.disk_read_bytes_per_second, 1048576);
	assert.equal(normalized?.system?.disk_write_bytes_per_second, 2097152);
	assert.equal(normalized?.system?.disk_sample_seconds, 10);
});

test('isServerStateEqual treats optional disk throughput changes as meaningful telemetry updates', () => {
	const baseline = normalizeServerState({
		server_id: 22,
		server_name: 'IoBox',
		host: 'iobox.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 12,
			ram_used: 1024,
			ram_total: 2048,
			io_pressure_some: 0,
			io_pressure_full: 0,
			io_blocked_tasks: 0,
			io_pressure_supported: true,
			disk_read_bytes_per_second: 1048576,
			disk_write_bytes_per_second: 2097152,
			disk_sample_seconds: 10
		},
		storage: null
	});
	const changed = normalizeServerState({
		server_id: 22,
		server_name: 'IoBox',
		host: 'iobox.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 12,
			ram_used: 1024,
			ram_total: 2048,
			io_pressure_some: 0,
			io_pressure_full: 0,
			io_blocked_tasks: 0,
			io_pressure_supported: true,
			disk_read_bytes_per_second: 1048576,
			disk_write_bytes_per_second: 3145728,
			disk_sample_seconds: 10
		},
		storage: null
	});

	assert.equal(isServerStateEqual(baseline, changed), false);
});

test('normalizeServerState preserves optional gpu inventory fields', () => {
	const normalized = normalizeServerState({
		server_id: 23,
		server_name: 'GpuBox',
		host: 'gpubox.internal',
		network: 'internal',
		status: 'degraded',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: null,
		storage: null,
		gpu_inventory: {
			state: 'partial',
			visible_count: 3,
			expected_count: 4,
			pci_count: 4,
			missing_indices: [2]
		}
	});

	assert.deepEqual(normalized?.gpu_inventory, {
		state: 'partial',
		visible_count: 3,
		expected_count: 4,
		pci_count: 4,
		missing_indices: [2]
	});
});

test('isServerStateEqual treats gpu inventory changes as meaningful telemetry updates', () => {
	const baseline = normalizeServerState({
		server_id: 24,
		server_name: 'GpuBox',
		host: 'gpubox.internal',
		network: 'internal',
		status: 'degraded',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: null,
		storage: null,
		gpu_inventory: {
			state: 'partial', visible_count: 3, expected_count: 4, pci_count: 4, missing_indices: [2]
		}
	});
	const changed = normalizeServerState({
		server_id: 24,
		server_name: 'GpuBox',
		host: 'gpubox.internal',
		network: 'internal',
		status: 'degraded',
		status_reason: null,
		last_seen: '2026-07-15T00:00:10Z',
		gpus: [],
		system: null,
		storage: null,
		gpu_inventory: {
			state: 'partial', visible_count: 2, expected_count: 4, pci_count: 4, missing_indices: [1, 2]
		}
	});

	assert.equal(isServerStateEqual(baseline, changed), false);
});

test('normalizeServerState preserves load-average, CPU PSI, and runnable-task fields', () => {
	const normalized = normalizeServerState({
		server_id: 31,
		server_name: 'QueueBox',
		host: 'queuebox.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-16T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 67,
			ram_used: 64000,
			ram_total: 128000,
			cpu_pressure_some: 8.5,
			cpu_running_tasks: 6,
			io_pressure_some: 0.8,
			io_pressure_full: 0.1,
			io_blocked_tasks: 2,
			io_pressure_supported: true,
			load_avg_1: 3.2,
			load_avg_5: 2.8,
			load_avg_15: 2.1,
			cpu_count: 32
		},
		storage: null
	});

	assert.equal(normalized?.system?.cpu_pressure_some, 8.5);
	assert.equal(normalized?.system?.cpu_running_tasks, 6);
	assert.equal(normalized?.system?.load_avg_1, 3.2);
	assert.equal(normalized?.system?.load_avg_5, 2.8);
	assert.equal(normalized?.system?.load_avg_15, 2.1);
	assert.equal(normalized?.system?.cpu_count, 32);
});

test('isServerStateEqual treats normalized load and CPU pressure changes as meaningful telemetry updates', () => {
	const baseline = normalizeServerState({
		server_id: 32,
		server_name: 'QueueBox',
		host: 'queuebox.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-16T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 67,
			ram_used: 64000,
			ram_total: 128000,
			cpu_pressure_some: 8.5,
			cpu_running_tasks: 6,
			io_pressure_some: 0.8,
			io_pressure_full: 0.1,
			io_blocked_tasks: 2,
			io_pressure_supported: true,
			load_avg_1: 3.2,
			load_avg_5: 2.8,
			load_avg_15: 2.1,
			cpu_count: 32
		},
		storage: null
	});
	const changed = normalizeServerState({
		server_id: 32,
		server_name: 'QueueBox',
		host: 'queuebox.internal',
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-16T00:00:10Z',
		gpus: [],
		system: {
			cpu_percent: 67,
			ram_used: 64000,
			ram_total: 128000,
			cpu_pressure_some: 12.5,
			cpu_running_tasks: 6,
			io_pressure_some: 0.8,
			io_pressure_full: 0.1,
			io_blocked_tasks: 2,
			io_pressure_supported: true,
			load_avg_1: 24.8,
			load_avg_5: 14.2,
			load_avg_15: 9.6,
			cpu_count: 32
		},
		storage: null
	});

	assert.equal(isServerStateEqual(baseline, changed), false);
});

