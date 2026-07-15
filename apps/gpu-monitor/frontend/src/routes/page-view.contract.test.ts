// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');

test('page menu uses the helper and never renders Default', () => {
	assert.match(pageSource, /dashboardViewLabel/);
	assert.doesNotMatch(pageSource, /\bDefault\b/);
});


const dashboardCss = readFileSync(new URL('../lib/styles/monitor-dashboard.css', import.meta.url), 'utf8');
const cardCss = readFileSync(new URL('../lib/styles/monitor-cards.css', import.meta.url), 'utf8');

function cssRule(source, selector) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = source.match(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`, 'm'));
	assert.ok(match?.groups?.body, `Missing CSS rule for ${selector}`);
	return match.groups.body;
}

function assertDeclaration(rule, property, value) {
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const escapedValue = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	assert.match(rule, new RegExp(`${escapedProperty}\\s*:\\s*${escapedValue}\\s*;`));
}

function functionBody(source, name, fromIndex = 0) {
	const start = source.indexOf(`function ${name}`, fromIndex);
	assert.notEqual(start, -1, `Missing function ${name}`);
	const open = source.indexOf('{', start);
	let depth = 0;
	for (let index = open; index < source.length; index += 1) {
		if (source[index] === '{') depth += 1;
		if (source[index] === '}') depth -= 1;
		if (depth === 0) return source.slice(open + 1, index);
	}
	throw new Error(`Could not parse function ${name}`);
}

test('task 2 full cards use 22rem density on the page shell and grid', () => {
	assert.match(pageSource, /const serverGridStyle = '--monitor-dashboard-card-min: 22rem;';/);

	const gridRule = cssRule(dashboardCss, '.monitor-dashboard-grid');
	assertDeclaration(gridRule, '--monitor-dashboard-card-min', '22rem');
	assertDeclaration(gridRule, 'gap', '0.9rem');

	const cardRule = cssRule(cardCss, '.monitor-card');
	assertDeclaration(cardRule, 'min-width', '22rem');
});

test('task 2 gpu fills and active G cue use exact chart tokens', () => {
	const activeIndexRule = cssRule(cardCss, ".monitor-gpu-row[data-active='true'] .monitor-gpu-row__index");
	assertDeclaration(activeIndexRule, 'background', 'var(--chart-2)');
	assertDeclaration(activeIndexRule, 'border-color', 'var(--chart-2)');
	assertDeclaration(activeIndexRule, 'color', '#040609');

	const utilRule = cssRule(cardCss, '.monitor-gpu-metric__fill--util');
	assertDeclaration(utilRule, 'background', 'var(--chart-2)');
	assert.doesNotMatch(utilRule, /color-mix/);

	const memoryRule = cssRule(cardCss, '.monitor-gpu-metric__fill--memory');
	assertDeclaration(memoryRule, 'background', 'var(--chart-1)');
	assert.doesNotMatch(memoryRule, /color-mix/);

	assert.doesNotMatch(
		cardCss,
		/\.monitor-gpu-row\[data-active='false'\][^{]*\.monitor-gpu-metric__fill--util[\s\S]*?background\s*:/
	);
});

test('task 2 card gpu list and footer use compact spacing', () => {
	const listRule = cssRule(cardCss, '.monitor-card__gpu-list');
	assertDeclaration(listRule, 'gap', '0.55rem');
	assertDeclaration(listRule, 'padding', '0 0.9rem 0.9rem');

	const footerRule = cssRule(cardCss, '.monitor-card__footer');
	assertDeclaration(footerRule, 'gap', '0.28rem');
	assertDeclaration(footerRule, 'padding', '0.5rem 0.75rem 0.55rem');
});


function cssRuleBody(source, selector) {
	return cssRule(source, selector).replace(/\s+/g, ' ').trim();
}

test('task 2 has no css zoom or dashboard scale wrappers in page dashboard card scope', () => {
	const scopedSources = [pageSource, dashboardCss, cardCss].join('\n');
	assert.doesNotMatch(scopedSources, /\bzoom\s*:/i);
	assert.doesNotMatch(pageSource, /scale\s*\(/i);
	assert.doesNotMatch(dashboardCss, /scale\s*\(/i);
	assert.doesNotMatch(cardCss, /\.monitor-dashboard[^{}]*\{[^}]*scale\s*\(/i);
});

test('task 2 preserves masonry grid behavior', () => {
	assert.match(pageSource, /class="monitor-dashboard-grid"[\s\S]*use:masonry=\{\$dashboardLayout === 'masonry'\}/);
	const masonryRule = cssRule(dashboardCss, '.monitor-dashboard-grid--masonry');
	assertDeclaration(masonryRule, 'grid-auto-rows', 'var(--monitor-dashboard-masonry-row)');
});

test('user server order remains movable and is shared by Full and Compact views', () => {
	assert.match(pageSource, /import \{ serverOrder, saveOrder \} from '\$lib\/stores\/order';/);
	assert.match(pageSource, /const currentServers = derived\([\s\S]*return orderServers\(selected, \$order\);[\s\S]*\);/);
	assert.match(pageSource, /draggable="true"|ondragstart=/);
	assert.match(pageSource, /ondragover=/);
	assert.match(pageSource, /ondrop=/);
	assert.match(pageSource, /ondragend=/);
	assert.match(pageSource, /<CompactDashboard servers=\{\$currentServers\} \/>/);
	assert.match(pageSource, /\{#each \$currentServers as server \(server\.server_id\)\}/);
});

test('View menu chooses aligned Grid or gapless Masonry without changing server order', () => {
	assert.match(pageSource, /\bdashboardLayout\b/);
	assert.match(pageSource, /\bsetDashboardLayout\b/);
	assert.match(pageSource, />그리드<\/button>/);
	assert.match(pageSource, />빈틈 없이<\/button>/);
	assert.match(pageSource, /use:masonry=\{\$dashboardLayout === 'masonry'\}/);
	assert.match(pageSource, /class:monitor-dashboard-grid--masonry=\{\$dashboardLayout === 'masonry'\}/);

	const gridRule = cssRule(dashboardCss, '.monitor-dashboard-grid');
	assertDeclaration(gridRule, 'grid-auto-rows', 'auto');
	const masonryRule = cssRule(dashboardCss, '.monitor-dashboard-grid--masonry');
	assertDeclaration(masonryRule, 'grid-auto-rows', 'var(--monitor-dashboard-masonry-row)');
});

test('task 1 masonry action writes and cleans stable grid placement properties', () => {
	assert.match(pageSource, /import \{\s*placeOrderedMasonryItems\s*\} from '\$lib\/utils\/orderedMasonry';/);
	assert.match(pageSource, /style\.gridColumnStart\s*=\s*String\(placement\.gridColumnStart\)/);
	assert.match(pageSource, /style\.gridRowStart\s*=\s*String\(placement\.gridRowStart\)/);
	assert.match(pageSource, /style\.gridRowEnd\s*=\s*placement\.gridRowEnd/);
	assert.match(pageSource, /style\.removeProperty\('grid-column-start'\)/);
	assert.match(pageSource, /style\.removeProperty\('grid-row-start'\)/);
	assert.match(pageSource, /style\.removeProperty\('grid-row-end'\)/);
});

test('task 2 leaves inactive gpu fills without desaturation overrides', () => {
	assert.doesNotMatch(
		cardCss,
		/\.monitor-gpu-row\[data-active='false'\][^{]*\.monitor-gpu-metric__fill--util[\s\S]*?background\s*:/
	);
	assert.doesNotMatch(
		cardCss,
		/\.monitor-gpu-row\[data-active='false'\][^{]*\.monitor-gpu-metric__fill--memory[\s\S]*?background\s*:/
	);
});

test('task 2 does not change system meter fill semantics to exact chart tokens', () => {
	const systemUtilRule = cssRuleBody(cardCss, '.monitor-meter__fill--util');
	assert.match(systemUtilRule, /background:\s*color-mix\(in srgb, var\(--ops-fg\) 42%, transparent\);/);
	assert.doesNotMatch(systemUtilRule, /var\(--chart-2\)/);

	const systemMemoryRule = cssRuleBody(cardCss, '.monitor-meter__fill--memory');
	assert.match(systemMemoryRule, /background:\s*color-mix\(in srgb, var\(--ops-primary\) 46%, transparent\);/);
	assert.doesNotMatch(systemMemoryRule, /var\(--chart-1\)/);
});

test('task 1 masonry layout clears stale placement before resize column detection and measurement', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const layoutBody = functionBody(pageSource, 'layout', masonryStart);
	const resetColumn = layoutBody.indexOf("style.removeProperty('grid-column-start')");
	const resetRowStart = layoutBody.indexOf("style.removeProperty('grid-row-start')");
	const resetRowEnd = layoutBody.indexOf("style.gridRowEnd = 'span 1'");
	const columnRead = layoutBody.indexOf('gridTemplateColumns');
	const heightRead = layoutBody.indexOf('getBoundingClientRect().height');

	assert.ok(resetColumn !== -1, 'layout must clear stale grid-column-start');
	assert.ok(resetRowStart !== -1, 'layout must clear stale grid-row-start');
	assert.ok(resetRowEnd !== -1, 'layout must reset grid-row-end before measuring');
	assert.ok(columnRead !== -1, 'layout must read actual gridTemplateColumns');
	assert.ok(heightRead !== -1, 'layout must measure item height');
	assert.ok(resetColumn < columnRead, 'grid-column-start must be cleared before column detection');
	assert.ok(resetRowStart < columnRead, 'grid-row-start must be cleared before column detection');
	assert.ok(resetRowEnd < heightRead, 'grid-row-end must be reset before measuring height');
});

test('persistent header shows semantic health and cadence without visible second-by-second copy', () => {
	const statusClass = pageSource.indexOf('class="ops-status"');
	assert.notEqual(statusClass, -1, 'Missing persistent header status');
	const statusStart = pageSource.lastIndexOf('<p', statusClass);
	const statusEnd = pageSource.indexOf('</p>', statusClass);
	assert.notEqual(statusEnd, -1, 'Missing persistent header status closing tag');
	const statusMarkup = pageSource.slice(statusStart, statusEnd);
	const statusBody = statusMarkup.slice(statusMarkup.indexOf('>') + 1);

	assert.match(statusMarkup, /\{refreshHealthText\(\)\}/);
	assert.match(statusMarkup, /class="ops-refresh-cadence"/);
	assert.match(statusMarkup, /class="ops-refresh-cadence__fill"/);
	assert.doesNotMatch(statusMarkup, /style=\{`width:/);
	assert.doesNotMatch(statusBody, /relativeTime\(|nextRefreshText\(/);
	assert.match(statusMarkup, /aria-label=\{`\$\{refreshHealthText\(\)\}[\s\S]*relativeTime\(lastRefreshAtMs\)[\s\S]*nextRefreshText\(\)/);
	assert.doesNotMatch(pageSource, /refreshCadencePct/);
});

test('desktop header reserves breathing room below the compact cadence line', () => {
	assert.match(
		dashboardCss,
		/@media \(min-width: 921px\)[\s\S]*?\.ops-header-inner,[\s\S]*?padding-block:\s*0\.45rem 0\.65rem;/
	);
	const cadenceRule = cssRule(dashboardCss, '.ops-refresh-cadence');
	assertDeclaration(cadenceRule, 'width', '2.4rem');
	assertDeclaration(cadenceRule, 'height', '0.2rem');
	const cadenceFillRule = cssRule(dashboardCss, '.ops-refresh-cadence__fill');
	assertDeclaration(cadenceFillRule, 'width', '42%');
	assert.match(cadenceFillRule, /animation:\s*ops-refresh-cadence-flow 2\.4s linear infinite/);
	assert.match(dashboardCss, /@keyframes ops-refresh-cadence-flow\s*\{[\s\S]*translate3d\(-120%, 0, 0\)[\s\S]*translate3d\(340%, 0, 0\)/);
});
