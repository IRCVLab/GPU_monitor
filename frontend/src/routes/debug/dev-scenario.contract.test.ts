// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');

test('debug page exposes a simulation-only control panel with corrected environment copy', () => {
	assert.match(pageSource, /SIMULATION/);
	assert.match(pageSource, /activeDevScenario/);
	assert.match(pageSource, /setDevScenario/);
	assert.match(pageSource, /resetDevScenario/);
	assert.match(pageSource, /applyDevScenario/);
	assert.match(pageSource, /DEV_SCENARIOS/);
	assert.match(pageSource, /aria-pressed=/);
	assert.match(pageSource, /title:\s*'normal'/);
	assert.match(pageSource, /title:\s*'stale'/);
	assert.match(pageSource, /title:\s*'io'/);
	assert.match(pageSource, /title:\s*'offline'/);
	assert.match(pageSource, /title:\s*'mixed'/);
	assert.match(pageSource, /client-side/i);
	assert.match(pageSource, /never writes API\/backend/i);
	assert.match(pageSource, /개발 백엔드 수집기[\s\S]*활성/);
	assert.match(pageSource, /Slack Socket Mode[\s\S]*비활성/);
	assert.doesNotMatch(pageSource, /GPU 수집기와 Slack Socket Mode가 비활성화/);
	assert.match(pageSource, /dashboard link/);
	assert.match(pageSource, /reset/);
});

test("production debug output omits the development scenario controls entirely", () => {
	assert.match(
		pageSource,
		/\{#if devMode\}\s*<section[^>]*data-dev-scenario-panel[\s\S]*<\/section>\s*\{\/if\}/
	);
});
