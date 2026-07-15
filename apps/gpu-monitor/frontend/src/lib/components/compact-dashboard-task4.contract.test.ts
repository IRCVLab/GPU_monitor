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

test('compact bank selector uses ordinary button pressed and current semantics', () => {
	assert.doesNotMatch(dashboardSource, /role="tablist"|role=\{'tablist'\}/);
	assert.doesNotMatch(dashboardSource, /role="tab"|role=\{'tab'\}/);
	assert.match(dashboardSource, /aria-pressed=\{bankIndex === activeBankIndex\}/);
	assert.match(dashboardSource, /aria-current=\{bankIndex === activeBankIndex \? 'true' : undefined\}/);
});

test('compact rows support absent placeholders, optional held overlays, and hide visible status copy', () => {
	assert.match(rowSource, /data-state=\{state\}/);
	assert.match(rowSource, /data-state="absent"|data-state=\{'absent'\}/);
	assert.match(rowSource, /data-held=\{heldGpuIndices\?\.has\(gpu\.index\) \? 'true' : undefined\}/);
	assert.doesNotMatch(rowSource, />\{statusConfig\[server\.status\].*label/);
	assert.match(cssSource, /\.compact-slot\[data-held='true'\]::after/);
});

test('compact rack css keeps eight fixed gpu columns and passive absent slots', () => {
	assert.doesNotMatch(cssSource, /repeat\(auto-fit/);
	assert.match(cssSource, /grid-template-columns:\s*clamp\(4\.5rem,\s*18vw,\s*8\.25rem\)\s*repeat\(8,\s*minmax\(22px,\s*1fr\)\)/);
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

test('compact mobile css keeps one row, disables per-cell touch, and preserves the row touch target', () => {
	assert.match(cssSource, /\.compact-row__select\s*\{[^}]*position:\s*absolute[^}]*inset:\s*0[^}]*cursor:\s*pointer/s);
	assert.match(cssSource, /@media \(max-width: 767px\) \{[\s\S]*\.compact-slot__users\s*\{[^}]*pointer-events:\s*none/s);
	assert.doesNotMatch(cssSource, /@media \(max-width: 767px\) \{[\s\S]*\.compact-row\s*\{[^}]*grid-template-columns\s*:/s);
	assert.doesNotMatch(cssSource, /@media \(max-width: 767px\) \{[\s\S]*\.compact-row\s*\{[^}]*display:\s*block/s);
	assert.doesNotMatch(cssSource, /@media \(max-width: 767px\) \{[\s\S]*\.compact-row\s*\{[^}]*display:\s*flex/s);
});

test('compact row primary activation preserves disclosure for occupied rows and exposes explicit full action in the popover', () => {
	assert.match(rowSource, /const occupiedSlots = \$derived\.by\(/);
	assert.match(rowSource, /onclick=\{\(event\) => handleRowActivation\(event\)\}/);
	assert.match(rowSource, /if \(occupiedSlots\.length > 0\) \{/);
	assert.match(rowSource, /openTooltip\(event\.currentTarget, popoverItems\(occupiedSlots\)\)/);
	assert.doesNotMatch(rowSource, /class="compact-row__select"[\s\S]*onclick=\{openFull\}/);
	assert.match(dashboardSource, />Full에서 보기</);
	assert.match(dashboardSource, /onmousedown=\{\(event\) => event\.preventDefault\(\)\}/);
	assert.match(dashboardSource, /onclick=\{\(\) => openFull\((activeTooltip\.serverId|tooltipServerId)\)\}/);
	assert.match(dashboardSource, /closeTooltip\(\);\s*onOpenFull\(serverId\);/);
});



test('compact occupied popover prioritizes GPU identity and full usernames', () => {
	assert.ok(!dashboardSource.includes('compact-dashboard__tooltip-state">사용 중'));
	const entryStart = dashboardSource.indexOf('class="compact-dashboard__tooltip-entry"');
	assert.notEqual(entryStart, -1);
	const entryMarkup = dashboardSource.slice(entryStart, dashboardSource.indexOf('</li>', entryStart));
	assert.doesNotMatch(entryMarkup, /aria-label=.*사용 중/);
	assert.ok(entryMarkup.indexOf('compact-dashboard__tooltip-gpu') < entryMarkup.indexOf('compact-dashboard__tooltip-users'));
	const entryRule = cssRule(cssSource, '.compact-dashboard__tooltip-entry');
	assert.ok(entryRule.includes('grid-template-columns: auto minmax(0, 1fr)'));
	assertDeclaration(entryRule, 'align-items', 'baseline');
	const usersRule = cssRule(cssSource, '.compact-dashboard__tooltip-users');
	assertDeclaration(usersRule, 'font-weight', '650');
});
