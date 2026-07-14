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

test("marks low idle baseline telemetry as available", () => {
	assert.equal(
		getCompactGpuState("online", "2026-07-14T00:00:00Z", {
			...baseGpu,
			utilization: 3,
			memory_used: 1_000
		}),
		"available"
	);
});

test("keeps empty-user but active-looking GPUs in unknown state", () => {
	assert.equal(
		getCompactGpuState("online", "2026-07-14T00:00:00Z", {
			...baseGpu,
			utilization: 11,
			memory_used: 7_000
		}),
		"unknown"
	);
});

test("never marks stale or offline telemetry as available", () => {
	assert.equal(getCompactGpuState("offline", "2026-07-14T00:00:00Z", baseGpu), "unknown");
	assert.equal(getCompactGpuState("online", null, baseGpu), "unknown");
});

test("prefers occupied when usernames are present", () => {
	assert.equal(
		getCompactGpuState("online", "2026-07-14T00:00:00Z", {
			...baseGpu,
			users: ["geonyeong"],
			utilization: 0,
			memory_used: 0
		}),
		"occupied"
	);
});
