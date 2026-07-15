// @ts-nocheck
import test from "node:test";
import assert from "node:assert/strict";

import { compactGpuBankCount, compactGpuBankSlots } from "./compactGpuMatrix.ts";

test("uses one eight-slot bank for G0-G7", () => {
	assert.equal(compactGpuBankCount([{ gpus: [{ index: 7 }] }]), 1);
});

test("adds a second bank when G8 exists", () => {
	assert.equal(compactGpuBankCount([{ gpus: [{ index: 8 }] }]), 2);
});

test("returns exact slots and null absent placeholders", () => {
	const slots = compactGpuBankSlots([{ index: 0 }, { index: 3 }], 0);
	assert.deepEqual(
		slots.map((gpu) => gpu?.index ?? null),
		[0, null, null, 3, null, null, null, null]
	);
});
