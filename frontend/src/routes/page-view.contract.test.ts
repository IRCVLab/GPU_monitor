// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');

test('page menu uses the helper and never renders Default', () => {
	assert.match(pageSource, /dashboardViewLabel/);
	assert.doesNotMatch(pageSource, /\bDefault\b/);
});
