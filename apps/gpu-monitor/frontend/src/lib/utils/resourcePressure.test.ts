// @ts-nocheck
import test from "node:test";
import assert from "node:assert/strict";
import {
	classifyPressure,
	pressureLabel,
	normalizeLoadRatio,
	classifyLoadRatio
} from "./resourcePressure.ts";

test("classifyPressure uses the Task 2 CPU and I/O PSI thresholds", () => {
	assert.equal(classifyPressure(null), "unknown");
	assert.equal(classifyPressure(4.9), "idle");
	assert.equal(classifyPressure(5), "pressure");
	assert.equal(classifyPressure(19.9), "pressure");
	assert.equal(classifyPressure(20), "bottleneck");
});

test("pressureLabel keeps the compact Korean severity copy", () => {
	assert.equal(pressureLabel("unknown"), "–");
	assert.equal(pressureLabel("idle"), "여유");
	assert.equal(pressureLabel("pressure"), "압박");
	assert.equal(pressureLabel("bottleneck"), "병목");
});

test("normalizeLoadRatio returns a finite queue-pressure ratio only for usable load telemetry", () => {
	assert.equal(normalizeLoadRatio(3.2, 32), 0.1);
	assert.equal(normalizeLoadRatio(3.2, 0), 3.2);
	assert.equal(normalizeLoadRatio(0, 64), 0);
	assert.equal(normalizeLoadRatio(null, 32), null);
	assert.equal(normalizeLoadRatio(3.2, null), null);
	assert.equal(normalizeLoadRatio(Number.NaN, 32), null);
	assert.equal(normalizeLoadRatio(-1, 32), null);
	assert.equal(normalizeLoadRatio(3.2, Number.POSITIVE_INFINITY), null);
});

test("classifyLoadRatio follows the htop-style normalized load thresholds", () => {
	assert.equal(classifyLoadRatio(null), "unknown");
	assert.equal(classifyLoadRatio(0.69), "idle");
	assert.equal(classifyLoadRatio(0.7), "pressure");
	assert.equal(classifyLoadRatio(0.99), "pressure");
	assert.equal(classifyLoadRatio(1), "bottleneck");
});
