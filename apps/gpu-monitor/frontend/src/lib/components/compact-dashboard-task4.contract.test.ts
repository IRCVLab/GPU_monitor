// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const dashboardSource = readFileSync(new URL('./CompactDashboard.svelte', import.meta.url), 'utf8');
const rowSource = readFileSync(new URL('./CompactServerRow.svelte', import.meta.url), 'utf8');
const detailSource = readFileSync(new URL('./CompactServerDetail.svelte', import.meta.url), 'utf8');
const pageSource = readFileSync(new URL('../../routes/+page.svelte', import.meta.url), 'utf8');
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

function assertAnyRuleDeclaration(source, selector, property, valuePattern) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const bodies = [...source.matchAll(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`, 'gm'))].map((match) =>
		match.groups.body.replace(/\s+/g, ' ').trim()
	);
	assert.ok(bodies.length > 0, `Missing CSS rule for ${selector}`);
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const declaration = new RegExp(`${escapedProperty}\\s*:\\s*${valuePattern}\\s*;`);
	assert.ok(bodies.some((body) => declaration.test(body)), `Missing ${property}: ${valuePattern} on ${selector}`);
}


function cssMediaRule(source, mediaQuery, selector) {
	const escapedMedia = mediaQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = source.match(
		new RegExp(`@media\\s*${escapedMedia}\\s*\\{[\\s\\S]*?${escapedSelector}\\s*\\{(?<body>[^}]*)\\}`, 'm')
	);
	assert.ok(match?.groups?.body, `Missing CSS rule for ${selector} in @media ${mediaQuery}`);
	return match.groups.body.replace(/\\s+/g, ' ').trim();
}

function numericRemValue(rule, property) {
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = rule.match(new RegExp(`${escapedProperty}\\s*:\\s*(?<value>[0-9.]+)rem\\s*;`));
	assert.ok(match?.groups?.value, `Missing ${property} rem declaration`);
	return Number(match.groups.value);
}

test('task 4 compact renders temporary detail only; no persistent rail or placeholder contracts remain', () => {
	assert.doesNotMatch(dashboardSource, /compact-dashboard__detail-panel/);
	assert.doesNotMatch(detailSource, /compact-detail__placeholder/);
	assert.doesNotMatch(detailSource, /mode\??:\s*'panel'/);
	assert.match(dashboardSource, /selectedServer\s*&&\s*isDesktop/);
	assert.match(dashboardSource, /class="compact-detail-overlay"/);
	assert.match(dashboardSource, /mode="overlay"/);
	assert.match(dashboardSource, /selectedServer\s*&&\s*!isDesktop/);
	assert.match(dashboardSource, /class="compact-sheet"[^>]*role="dialog"[^>]*aria-modal="true"/);
});

test('task 4 compact list is availability-only and does not expose network, ip, or freshness metadata', () => {
	const compactSources = [dashboardSource, rowSource, detailSource].join('\n');
	assert.doesNotMatch(compactSources, /showNetwork/);
	assert.doesNotMatch(compactSources, /formatNetwork/);
	assert.doesNotMatch(compactSources, /compact-(?:row|detail)__network/);
	assert.doesNotMatch(compactSources, /\bfresh(?:ness)?\b|\bhost\b|\bport\b/i);
	assert.doesNotMatch(pageSource, /<CompactDashboard[^>]*showNetwork=/);
});

test('task 3 compact rows stay one-line through tablet widths and stack only on mobile', () => {
	const dashboardRule = cssRule(cssSource, '.compact-dashboard');
	assertDeclaration(dashboardRule, 'min-width', '0');
	assertDeclaration(dashboardRule, 'overflow-x', 'clip');
	assert.doesNotMatch(cssSource, /\.compact-dashboard\s*\{[^}]*grid-template-columns\s*:\s*minmax\(0, 1\.9fr\) minmax\(16\.5rem, 0\.72fr\)/s);

	const listRule = cssRule(cssSource, '.compact-dashboard__list');
	assertDeclaration(listRule, 'min-width', '0');
	assertDeclaration(listRule, 'overflow-x', 'clip');

	const rowRule = cssRule(cssSource, '.compact-row');
	assertDeclaration(rowRule, 'min-width', '0');
	assertDeclaration(rowRule, 'overflow-x', 'clip');
	assertDeclaration(rowRule, 'grid-template-columns', 'minmax\\(7rem, 8\\.5rem\\) minmax\\(0, 1fr\\)');
	assert.ok(numericRemValue(rowRule, 'min-height') <= 2.7, 'Base compact row min-height must be <= 2.7rem');

	const tabletRowRule = cssMediaRule(cssSource, '(max-width: 1199px)', '.compact-row');
	assertDeclaration(tabletRowRule, 'grid-template-columns', 'minmax\\(7rem, 8\\.5rem\\) minmax\\(0, 1fr\\)');
	assert.ok(numericRemValue(tabletRowRule, 'min-height') <= 2.7, 'Tablet compact row min-height must be <= 2.7rem so 1024px remains one visual server row');

	const mobileRowRule = cssMediaRule(cssSource, '(max-width: 767px)', '.compact-row');
	assertDeclaration(mobileRowRule, 'grid-template-columns', 'minmax\\(0, 1fr\\)');

	const slotRule = cssRule(cssSource, '.compact-slot');
	assert.ok(numericRemValue(slotRule, 'height') <= 1.8, 'Compact slot height must be <= 1.8rem');

	assertAnyRuleDeclaration(
		cssSource,
		'.compact-row__slots',
		'grid-template-columns',
		'repeat\\(auto-fit, minmax\\(2\\.6rem, 1fr\\)\\)'
	);
});

test('compact gpu slots keep available dark and make occupied a restrained accent tint', () => {
	assert.match(rowSource, /data-state=\{state\}/);
	const availableRule = cssRule(cssSource, ".compact-slot[data-state='available']");
	assert.match(availableRule, /var\(--chart-2\)/);
	assert.match(availableRule, /var\(--ops-card\)/);
	const occupiedRule = cssRule(cssSource, ".compact-slot[data-state='occupied']");
	assert.match(occupiedRule, /background:\s*color-mix\(in srgb, var\(--chart-2\) 1[0-8]%, var\(--ops-card\)\)/);
	assert.match(occupiedRule, /border-color:\s*color-mix\(in srgb, var\(--chart-2\) [234][0-9]%, var\(--ops-border\)\)/);
	assert.doesNotMatch(occupiedRule, /background:\s*var\(--chart-2\)/);
	const occupiedDetailRule = cssRule(cssSource, ".compact-detail__gpu[data-state='occupied']");
	assert.match(occupiedDetailRule, /background:\s*color-mix\(in srgb, var\(--chart-2\) 1[0-8]%, var\(--ops-card\)\)/);
	assert.doesNotMatch(occupiedDetailRule, /background:\s*var\(--chart-2\)/);
	const unknownRule = cssRule(cssSource, ".compact-slot[data-state='unknown']");
	assert.doesNotMatch(unknownRule, /#f59e0b|var\(--chart-[12]\)/);
});
