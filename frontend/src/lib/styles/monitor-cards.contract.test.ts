// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('./monitor-cards.css', import.meta.url), 'utf8');

test('hold footer remains chip-based and compact', () => {
	assert.match(css, /\.note-form-kind-row/);
	assert.match(css, /\.note-form-kind-toggle/);
	assert.match(css, /\.note-form-hold-chip-row/);
	assert.match(css, /\.note-form-hold-chip/);
	assert.match(css, /\.note-form-hold-warning/);
	assert.match(css, /\.monitor-note-item__kind/);
	assert.match(css, /\.monitor-note-item__gpu-chips/);
	assert.match(css, /\.monitor-note-item__gpu-chip/);
});
