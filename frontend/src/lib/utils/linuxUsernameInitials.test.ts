// @ts-nocheck
import test from "node:test";
import assert from "node:assert/strict";

import { getLinuxUsernameInitials } from "./linuxUsernameInitials.ts";

test("returns fallback initials and seed for empty input", () => {
	assert.deepStrictEqual(getLinuxUsernameInitials("   "), {
		initials: "?",
		seed: 0
	});
});

test("trims input and returns uppercase initials for one-token usernames", () => {
	assert.deepStrictEqual(getLinuxUsernameInitials("  ada  "), {
		initials: "AD",
		seed: getLinuxUsernameInitials("ada").seed
	});
});

test("uses the first character from the first two username tokens", () => {
	assert.equal(getLinuxUsernameInitials("grace hopper").initials, "GH");
	assert.equal(getLinuxUsernameInitials("john.doe").initials, "JD");
});

test("handles punctuation-heavy linux usernames deterministically", () => {
	const first = getLinuxUsernameInitials("gpu-user_01");
	const second = getLinuxUsernameInitials("gpu-user_01");

	assert.equal(first.initials, "GU");
	assert.equal(second.initials, "GU");
	assert.equal(first.seed, second.seed);
});

test("returns a one- or two-character uppercase label and a stable seed", () => {
	const result = getLinuxUsernameInitials("x");

	assert.match(result.initials, /^[A-Z0-9?]{1,2}$/);
	assert.equal(result.seed, getLinuxUsernameInitials("x").seed);
});
