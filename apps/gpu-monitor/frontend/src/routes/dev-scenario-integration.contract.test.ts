// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
const debugPageSource = readFileSync(new URL('./debug/+page.svelte', import.meta.url), 'utf8');
const debugPageModuleUrl = new URL('./debug/+page.ts', import.meta.url);
const debugPageModuleExists = existsSync(debugPageModuleUrl);
const debugPageModuleSource = debugPageModuleExists ? readFileSync(debugPageModuleUrl, 'utf8') : '';

test('development scenario is applied after user ordering and feeds both dashboard views', () => {
	assert.match(pageSource, /activeDevScenario/);
	assert.match(pageSource, /applyDevScenario/);
	assert.match(
		pageSource,
		/const displayServers = derived\(\s*\[currentServers, activeDevScenario\][\s\S]*applyDevScenario\(\$servers, \$scenario/
	);
	assert.match(pageSource, /<CompactDashboard servers=\{\$displayServers\}/);
	assert.match(pageSource, /\{#each \$displayServers as server \(server\.server_id\)\}/);
	assert.match(pageSource, /const list = \[\.\.\.get\(currentServers\)\]/);
});

test('active simulation is unmistakable and can be reset without backend writes', () => {
	assert.match(pageSource, /import\s*\{[^}]*\bdev\b[^}]*\}\s*from\s*['"]\$app\/environment['"]/);
	assert.match(pageSource, /\$activeDevScenario !== 'normal'/);
	assert.match(pageSource, /SIMULATION/);
	assert.match(pageSource, /실제 서버 데이터는 변경되지 않습니다/);
	assert.match(pageSource, /gpu_missing: 'GPU visibility mismatch · GPU 누락'/);
	assert.match(pageSource, /onclick=\{resetDevScenario\}/);
	assert.doesNotMatch(pageSource, /setDevScenario[\s\S]*fetch\(|resetDevScenario[\s\S]*fetch\(/);
});

test('production dashboard never applies local development scenarios', () => {
	assert.match(
		pageSource,
		/const displayServers = derived\(\s*\[currentServers, activeDevScenario\][\s\S]*dev\s+\?\s+applyDevScenario\(\$servers, \$scenario, Date\.now\(\)\)\s+:\s+\$servers/
	);
});

test('dashboard debug links and simulation banner render only in development', () => {
	assert.match(
		pageSource,
		/\{#if dev\}\s*<a class="ops-menu-link" href="\/debug">개발 진단<\/a>\s*\{\/if\}/
	);
	assert.match(
		pageSource,
		/\{#if dev && \$activeDevScenario !== 'normal'\}\s*<aside class="monitor-dev-simulation"/
	);
});

test('debug route returns not found outside local development', () => {
	assert.equal(debugPageModuleExists, true);
	assert.match(debugPageModuleSource, /import\s*\{[^}]*\bdev\b[^}]*\}\s*from\s*['"]\$app\/environment['"]/);
	assert.match(debugPageModuleSource, /import\s*\{[^}]*\berror\b[^}]*\}\s*from\s*['"]@sveltejs\/kit['"]/);
	assert.match(debugPageModuleSource, /export const load\s*=\s*\(\)\s*=>\s*\{/);
	assert.match(debugPageModuleSource, /if\s*\(!dev\)\s*error\(404,\s*['"]Not found['"]\)/);
});

test('debug page presents six scenarios including GPU missing metadata in a responsive layout', () => {
	assert.match(debugPageSource, /gpu_missing/);
	assert.match(debugPageSource, /GPU visibility mismatch|GPU 누락/);
	assert.match(debugPageSource, /xl:grid-cols-6/);
	assert.match(debugPageSource, /DEV_SCENARIOS/);
});
