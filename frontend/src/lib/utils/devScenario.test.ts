// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

const moduleUrl = new URL('./devScenario.ts', import.meta.url);

async function loadDevScenarioModule() {
	assert.equal(existsSync(moduleUrl), true, 'Missing devScenario.ts');
	return import(moduleUrl.href);
}

function createState(overrides = {}) {
	return {
		server_id: 10,
		server_name: 'alpha',
		host: 'alpha.internal',
		port: 22,
		network: 'internal',
		status: 'online',
		status_reason: null,
		last_seen: '2026-07-16T00:00:00.000Z',
		gpus: [
			{
				index: 0,
				name: 'RTX 6000',
				utilization: 2,
				memory_used: 512,
				memory_total: 48_000,
				temperature: 41,
				power_draw: 85,
				users: []
			}
		],
		system: {
			cpu_percent: 31,
			ram_used: 32_768,
			ram_total: 65_536,
			io_pressure_some: 0.4,
			io_pressure_full: 0,
			io_blocked_tasks: 0,
			io_pressure_supported: true
		},
		storage: null,
		display_order: 10,
		...overrides
	};
}

function createOrderedStates() {
	return [
		createState({ server_id: 42, server_name: 'delta', display_order: 4 }),
		createState({ server_id: 7, server_name: 'beta', display_order: 1 }),
		createState({ server_id: 19, server_name: 'gamma', display_order: 8 })
	];
}

test('dev scenario util exports the supported simulator API', async () => {
	const mod = await loadDevScenarioModule();
	assert.deepEqual(mod.DEV_SCENARIOS, ['normal', 'stale', 'io', 'offline', 'mixed']);
	assert.equal(typeof mod.applyDevScenario, 'function');
});

test('normal scenario is a no-op and does not add warnings to healthy telemetry', async () => {
	const { applyDevScenario } = await loadDevScenarioModule();
	const input = [createState()];
	const result = applyDevScenario(input, 'normal', Date.parse('2026-07-16T00:01:30Z'));

	assert.equal(result, input);
	assert.equal(result[0].status, 'online');
	assert.equal(result[0].status_reason, null);
	assert.equal(result[0].last_seen, '2026-07-16T00:00:00.000Z');
});

test('stale scenario only rewrites the first server immutably and preserves order', async () => {
	const { applyDevScenario } = await loadDevScenarioModule();
	const nowMs = Date.parse('2026-07-16T00:10:00.000Z');
	const input = createOrderedStates();
	const original = structuredClone(input);
	const result = applyDevScenario(input, 'stale', nowMs);

	assert.deepEqual(result.map((state) => state.server_id), [42, 7, 19]);
	assert.notEqual(result, input);
	assert.notEqual(result[0], input[0]);
	assert.equal(result[1], input[1]);
	assert.equal(result[0].status, 'online');
	assert.equal(result[0].last_seen, '2026-07-16T00:08:30.000Z');
	assert.equal(result[0].status_reason?.code, 'dev-sim-stale');
	assert.match(result[0].status_reason?.message ?? '', /stale/i);
	assert.deepEqual(input, original);
});

test('io scenario injects PSI pressure values and a bottleneck reason without mutating source data', async () => {
	const { applyDevScenario } = await loadDevScenarioModule();
	const nowMs = Date.parse('2026-07-16T00:10:00.000Z');
	const input = createOrderedStates();
	const original = structuredClone(input);
	const result = applyDevScenario(input, 'io', nowMs);

	assert.equal(result[0].status, 'online');
	assert.equal(result[0].status_reason?.code, 'dev-sim-io');
	assert.match(result[0].status_reason?.message ?? '', /bottleneck/i);
	assert.equal(result[0].system?.io_pressure_some, 38.4);
	assert.equal(result[0].system?.io_pressure_full, 12.8);
	assert.equal(result[0].system?.io_blocked_tasks, 4);
	assert.equal(result[0].system?.io_pressure_supported, true);
	assert.deepEqual(input, original);
});

test('offline scenario forces a timeout snapshot about five minutes old', async () => {
	const { applyDevScenario } = await loadDevScenarioModule();
	const nowMs = Date.parse('2026-07-16T00:10:00.000Z');
	const input = createOrderedStates();
	const original = structuredClone(input);
	const result = applyDevScenario(input, 'offline', nowMs);

	assert.equal(result[0].status, 'offline');
	assert.equal(result[0].last_seen, '2026-07-16T00:05:00.000Z');
	assert.equal(result[0].status_reason?.code, 'dev-sim-offline');
	assert.match(result[0].status_reason?.message ?? '', /SSH timeout/i);
	assert.deepEqual(input, original);
});

test('mixed scenario deterministically applies stale, io, and offline to the first three servers', async () => {
	const { applyDevScenario } = await loadDevScenarioModule();
	const nowMs = Date.parse('2026-07-16T00:10:00.000Z');
	const input = createOrderedStates();
	const result = applyDevScenario(input, 'mixed', nowMs);

	assert.deepEqual(result.map((state) => state.server_id), [42, 7, 19]);
	assert.equal(result[0].status, 'online');
	assert.equal(result[0].status_reason?.code, 'dev-sim-stale');
	assert.equal(result[1].status, 'online');
	assert.equal(result[1].status_reason?.code, 'dev-sim-io');
	assert.equal(result[1].system?.io_pressure_some, 38.4);
	assert.equal(result[2].status, 'offline');
	assert.equal(result[2].status_reason?.code, 'dev-sim-offline');
});
