// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const componentUrl = new URL('./RefreshRing.svelte', import.meta.url);
const source = existsSync(fileURLToPath(componentUrl)) ? readFileSync(componentUrl, 'utf8') : '';

test('shared refresh ring renders a status dot and one-unit svg progress path', () => {
	assert.ok(source, 'RefreshRing.svelte must exist');
	assert.match(source, /cycleKey/);
	assert.match(source, /durationMs/);
	assert.match(source, /variant\?:\s*'header'\s*\|\s*'floating'/);
	assert.match(source, /<svg[^>]*class="ops-refresh-ring__svg"/);
	assert.match(source, /<circle[^>]*class="ops-refresh-ring__track"[^>]*pathLength="1"/);
	assert.match(source, /\{#key cycleKey\}[\s\S]*<circle[^>]*class="ops-refresh-ring__progress"[^>]*pathLength="1"/);
	assert.match(source, /class:attention=\{attention\}/);
	assert.match(source, /class="ops-refresh-ring__dot"/);
});

test('refresh ring exposes no standalone text or interactive control', () => {
	assert.doesNotMatch(source, /<button|tabindex=|onclick=/);
	assert.match(source, /aria-hidden="true"/);
});
