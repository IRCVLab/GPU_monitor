// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const dashboardSource = readFileSync(new URL('./CompactDashboard.svelte', import.meta.url), 'utf8');
const rowSource = readFileSync(new URL('./CompactServerRow.svelte', import.meta.url), 'utf8');
const cssSource = readFileSync(new URL('../styles/monitor-compact.css', import.meta.url), 'utf8');

function cssRule(source, selector) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = source.match(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`, 'm'));
	assert.ok(match?.groups?.body, `Missing CSS rule for ${selector}`);
	return match.groups.body.replace(/\s+/g, ' ').trim();
}

function assertDeclaration(rule, property, valuePattern) {
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	assert.match(rule, new RegExp(`${escapedProperty}\\s*:\\s*${valuePattern}\\s*;`));
}

test('compact dashboard removes the inspector flow and renders a fixed banked matrix contract', () => {
	assert.doesNotMatch(dashboardSource, /CompactServerDetail|compact-dashboard__inspector|compact-sheet/);
	assert.match(dashboardSource, /compactGpuBankCount/);
	assert.match(dashboardSource, /compactGpuBankSlots/);
	assert.match(dashboardSource, /compact-dashboard__column-header/);
});

test('compact rows support absent placeholders, optional held notches, and hide visible status copy', () => {
	assert.match(rowSource, /data-state="absent"|data-state=\{'absent'\}/);
	assert.match(rowSource, /data-held/);
	assert.doesNotMatch(rowSource, />\{statusConfig\[server\.status\].*label/);
});

test('compact rack css keeps eight fixed gpu columns and passive absent slots', () => {
	assert.doesNotMatch(cssSource, /repeat\(auto-fit/);
	assert.match(cssSource, /repeat\(8,\s*minmax\(22px,\s*1fr\)\)/);
	assert.doesNotMatch(cssSource, /grid-template-columns:\s*minmax\(0,\s*1fr\)/);

	const availableRule = cssRule(cssSource, ".compact-slot[data-state='available']");
	assert.match(availableRule, /var\(--ops-primary\)/);
	assert.ok(/border[^;]*var\(--ops-primary\)|outline[^;]*var\(--ops-primary\)|box-shadow:[^;]*var\(--ops-primary\)/.test(availableRule), 'available slots must use a var(--ops-primary) outline');

	const occupiedRule = cssRule(cssSource, ".compact-slot[data-state='occupied']");
	assert.match(occupiedRule, /var\(--ops-primary\)/);
	assert.ok(/background:\s*[^;]*var\(--ops-primary\)/.test(occupiedRule), 'occupied slots must use selected theme fill');

	const unknownRule = cssRule(cssSource, ".compact-slot[data-state='unknown']");
	assert.doesNotMatch(unknownRule, /var\(--ops-primary\)|#22c55e|var\(--chart-2\)/);

	const absentRule = cssRule(cssSource, ".compact-slot[data-state='absent']");
	assertDeclaration(absentRule, 'pointer-events', 'none');
	assert.match(absentRule, /background:\s*transparent/);
});
