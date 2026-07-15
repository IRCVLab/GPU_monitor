// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const sourceUrl = new URL('./devScenario.ts', import.meta.url);

test('dev scenario store stays SSR-safe, dev-only, and session-scoped', () => {
	assert.equal(existsSync(sourceUrl), true, 'Missing devScenario store');
	const source = readFileSync(sourceUrl, 'utf8');

	assert.match(source, /import \{ browser \} from '\$app\/environment';/);
	assert.match(source, /import\.meta\.env\.DEV/);
	assert.match(source, /sessionStorage/);
	assert.doesNotMatch(source, /localStorage/);
	assert.match(source, /export const activeDevScenario/);
	assert.match(source, /export function setDevScenario\(value: DevScenario\): void/);
	assert.match(source, /export function resetDevScenario\(\): void/);
	assert.match(source, /sessionStorage\.setItem/);
	assert.match(source, /sessionStorage\.removeItem/);
});
