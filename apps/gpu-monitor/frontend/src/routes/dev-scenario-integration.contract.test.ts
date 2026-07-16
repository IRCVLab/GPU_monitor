// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
const debugPageSource = readFileSync(new URL('./debug/+page.svelte', import.meta.url), 'utf8');

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
	assert.match(pageSource, /import\.meta\.env\.DEV/);
	assert.match(pageSource, /\$activeDevScenario !== 'normal'/);
	assert.match(pageSource, /SIMULATION/);
	assert.match(pageSource, /실제 서버 데이터는 변경되지 않습니다/);
	assert.match(pageSource, /gpu_missing: 'GPU visibility mismatch · GPU 누락'/);
	assert.match(pageSource, /onclick=\{resetDevScenario\}/);
	assert.doesNotMatch(pageSource, /setDevScenario[\s\S]*fetch\(|resetDevScenario[\s\S]*fetch\(/);
});


test('debug page presents six scenarios including GPU missing metadata in a responsive layout', () => {
	assert.match(debugPageSource, /gpu_missing/);
	assert.match(debugPageSource, /GPU visibility mismatch|GPU 누락/);
	assert.match(debugPageSource, /xl:grid-cols-6/);
	assert.match(debugPageSource, /DEV_SCENARIOS/);
});
