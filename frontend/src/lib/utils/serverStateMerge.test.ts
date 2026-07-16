// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeServerRecordState } from './serverStateMerge.ts';
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
