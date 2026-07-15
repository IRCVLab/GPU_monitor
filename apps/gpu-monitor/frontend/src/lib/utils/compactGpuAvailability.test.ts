// @ts-nocheck
import test from "node:test";
import assert from "node:assert/strict";

import { getCompactGpuState } from "./compactGpuAvailability.ts";

const baseGpu = {
	index: 0,
	name: "NVIDIA RTX 6000",
	utilization: 0,
	memory_used: 0,
	memory_total: 48_000,
	temperature: 0,
	power_draw: 0,
	users: []
};

const lastSeen = "2026-07-14T00:00:00Z";
const freshNowMs = Date.parse("2026-07-14T00:00:45Z");
const staleNowMs = Date.parse("2026-07-14T00:01:01Z");

test("marks low idle baseline telemetry as available while telemetry is fresh", () => {
	assert.equal(
		getCompactGpuState(
			"online",
			lastSeen,
			{
				...baseGpu,
				utilization: 3,
				memory_used: 1_000
			},
			{ nowMs: freshNowMs }
		),
		"available"
	);
});

test("keeps empty-user but active-looking GPUs in unknown state", () => {
	assert.equal(
		getCompactGpuState(
			"online",
			lastSeen,
			{
				...baseGpu,
				utilization: 11,
				memory_used: 7_000
			},
			{ nowMs: freshNowMs }
		),
		"unknown"
	);
});

test("treats old idle telemetry timestamps as unknown", () => {
	assert.equal(
		getCompactGpuState(
			"online",
			lastSeen,
			{
				...baseGpu,
				utilization: 3,
				memory_used: 1_000
			},
			{ nowMs: staleNowMs }
		),
		"unknown"
	);
});

test("never marks offline or missing telemetry as available", () => {
	assert.equal(getCompactGpuState("offline", lastSeen, baseGpu, { nowMs: freshNowMs }), "unknown");
	assert.equal(getCompactGpuState("online", null, baseGpu, { nowMs: freshNowMs }), "unknown");
});

test("prefers occupied when usernames are present", () => {
	assert.equal(
		getCompactGpuState(
			"online",
			lastSeen,
			{
				...baseGpu,
				users: ["geonyeong"],
				utilization: 0,
				memory_used: 0
			},
			{ nowMs: freshNowMs }
		),
		"occupied"
	);
});
