// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./order.ts', import.meta.url), 'utf8');

test('server order reads and writes only the serverOrder cookie', () => {
	assert.match(source, /import \{ readCookie, writeCookie \} from '\$lib\/utils\/cookies';/);
	assert.match(source, /const STORAGE_KEY = 'serverOrder';/);
	assert.match(source, /function readOrder\(\): number\[\] \{[\s\S]*readCookie\(STORAGE_KEY\)/);
	assert.match(source, /export const serverOrder = writable<number\[\]>\(readOrder\(\)\);/);
	assert.match(source, /export async function saveOrder\(ids: number\[\]\): Promise<void> \{[\s\S]*writeCookie\(STORAGE_KEY, normalized\.join\(','\)\)/);
});

test('server order persistence normalizes to unique positive integer ids and avoids local or backend sync', () => {
	assert.match(source, /Number\.isInteger\(value\) && value > 0 && list\.indexOf\(value\) === index/);
	assert.doesNotMatch(source, /localStorage|sessionStorage|fetch\(|navigator\.sendBeacon|WebSocket|EventSource|BroadcastChannel|serviceWorker/i);
});
