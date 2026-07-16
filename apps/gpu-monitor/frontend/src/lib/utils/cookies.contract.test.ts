// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./cookies.ts', import.meta.url), 'utf8');

test('shared writeCookie persists preferences for one year on the app path with lax same-site policy', () => {
	assert.match(source, /const ONE_YEAR_SECONDS = 60 \* 60 \* 24 \* 365/);
	assert.match(source, /export function writeCookie\(name: string, value: string, maxAge = ONE_YEAR_SECONDS\): void/);
	assert.match(source, /document\.cookie = `\$\{name\}=\$\{encodeURIComponent\(value\)\}; path=\/; max-age=\$\{maxAge\}; SameSite=Lax`/);
	assert.match(source, /31536000|60 \* 60 \* 24 \* 365/);
});

test('shared readCookie decodes the named cookie without falling back to local or remote persistence', () => {
	assert.match(source, /export function readCookie\(name: string\): string \| null/);
	assert.match(source, /document\.cookie\.match\(new RegExp/);
	assert.match(source, /decodeURIComponent\(match\[1\]\)/);
	assert.doesNotMatch(source, /localStorage|sessionStorage|fetch\(|navigator\.sendBeacon|WebSocket|EventSource|sync/i);
});
