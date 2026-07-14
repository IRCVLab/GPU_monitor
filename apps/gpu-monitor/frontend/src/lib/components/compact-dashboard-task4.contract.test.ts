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

test('task 4 compact CSS is full-width wrapping with no horizontal row or page scroll', () => {
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
	assertDeclaration(rowRule, 'grid-template-columns', 'minmax\\(0, 1fr\\)');

	assertAnyRuleDeclaration(
		cssSource,
		'.compact-row__slots',
		'grid-template-columns',
		'repeat\\(auto-fit, minmax\\(2\\.6rem, 1fr\\)\\)'
	);
});
