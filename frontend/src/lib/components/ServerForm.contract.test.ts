// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./ServerForm.svelte', import.meta.url), 'utf8');

test('closed server form is inert as well as aria-hidden', () => {
	assert.match(source, /<aside[\s\S]*?inert=\{!open\}[\s\S]*?aria-hidden=\{!open\}/);
});

test('global Escape handler closes only an open server form', () => {
	assert.match(source, /if \(open && e\.key === 'Escape'\) onClose\(\);/);
});
