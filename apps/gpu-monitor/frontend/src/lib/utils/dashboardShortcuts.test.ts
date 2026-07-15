// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveDashboardShortcut } from './dashboardShortcuts.ts';

function keyboardEvent(overrides = {}) {
	return {
		code: '',
		key: '',
		repeat: false,
		isComposing: false,
		defaultPrevented: false,
		ctrlKey: false,
		metaKey: false,
		altKey: false,
		shiftKey: false,
		target: null,
		...overrides
	};
}

test('physical shortcut codes work independently of the active input language', () => {
	assert.deepEqual(
		resolveDashboardShortcut(keyboardEvent({ code: 'KeyV', key: 'ㅍ' })),
		{ type: 'toggle-view' }
	);
	assert.deepEqual(
		resolveDashboardShortcut(keyboardEvent({ code: 'KeyC', key: 'ㅊ' })),
		{ type: 'toggle-theme' }
	);
});

test('number row and numpad shortcuts select internal external and all in user order', () => {
	assert.deepEqual(resolveDashboardShortcut(keyboardEvent({ code: 'Digit1' })), {
		type: 'select-network',
		tab: 'internal'
	});
	assert.deepEqual(resolveDashboardShortcut(keyboardEvent({ code: 'Digit2' })), {
		type: 'select-network',
		tab: 'external'
	});
	assert.deepEqual(resolveDashboardShortcut(keyboardEvent({ code: 'Digit3' })), {
		type: 'select-network',
		tab: 'all'
	});
	assert.deepEqual(resolveDashboardShortcut(keyboardEvent({ code: 'Numpad2' })), {
		type: 'select-network',
		tab: 'external'
	});
});

test('shortcuts stay inert while the user is editing or composing text', () => {
	const input = { tagName: 'INPUT', isContentEditable: false, closest: () => null };
	const editable = { tagName: 'DIV', isContentEditable: true, closest: () => null };

	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'KeyV', target: input })), null);
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'KeyC', target: editable })), null);
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'Digit1', isComposing: true })), null);
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'Digit2', repeat: true })), null);
});

test('shortcuts do not override browser or operating-system modifier chords', () => {
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'KeyV', metaKey: true })), null);
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'KeyC', ctrlKey: true })), null);
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'Digit1', altKey: true })), null);
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'KeyV', defaultPrevented: true })), null);
});

test('letter shortcuts are case-insensitive while shifted number symbols stay untouched', () => {
	assert.deepEqual(resolveDashboardShortcut(keyboardEvent({ code: 'KeyV', shiftKey: true })), {
		type: 'toggle-view'
	});
	assert.deepEqual(resolveDashboardShortcut(keyboardEvent({ code: 'KeyC', shiftKey: true })), {
		type: 'toggle-theme'
	});
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'Digit1', shiftKey: true })), null);
	assert.equal(resolveDashboardShortcut(keyboardEvent({ code: 'Numpad3', shiftKey: true })), null);
});
