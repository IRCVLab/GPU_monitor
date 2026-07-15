// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const pageSource = readFileSync(new URL('./+page.svelte', import.meta.url), 'utf8');
const compactDashboardSource = readFileSync(new URL('../lib/components/CompactDashboard.svelte', import.meta.url), 'utf8');
const compactServerRowSource = readFileSync(new URL('../lib/components/CompactServerRow.svelte', import.meta.url), 'utf8');

test('page menu uses the helper and never renders Default', () => {
	assert.match(pageSource, /dashboardViewLabel/);
	assert.doesNotMatch(pageSource, /\bDefault\b/);
});

test('task 3 keeps view layout controls contextual and wires compact-to-full continuity focus', () => {
	assert.match(pageSource, /\{#if \$dashboardView === 'default'\}[\s\S]*카드 배치/);
	assert.match(pageSource, /<CompactDashboard[\s\S]*onOpenFull=/);
	assert.match(pageSource, /focusedServerId/);
	assert.match(pageSource, /\{#each \$currentServers as server \(server\.server_id\)\}/);
	assert.match(compactDashboardSource, /<CompactServerRow[\s\S]*onOpenFull=/);
	assert.match(compactDashboardSource, /Full에서 보기/);
	assert.match(compactServerRowSource, /onOpenFull\?: \(serverId: number\) => void/);
	assert.match(compactServerRowSource, /onclick=\{\(event\) => handleRowActivation\(event\)\}/);
	assert.match(compactServerRowSource, /if \(occupiedSlots\.length > 0\) \{[\s\S]*openTooltip\(event\.currentTarget, popoverItems\(occupiedSlots\)\)/);
	assert.match(compactServerRowSource, /\t\topenFull\(\);/);
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

test('task 2 grid owns 22rem density while cards can shrink inside narrow viewports', () => {
	assert.match(pageSource, /const serverGridStyle = '--monitor-dashboard-card-min: 22rem;';/);

	const gridRule = cssRule(dashboardCss, '.monitor-dashboard-grid');
	assertDeclaration(gridRule, '--monitor-dashboard-card-min', '22rem');
	assertDeclaration(gridRule, 'gap', '0.9rem');

	const cardRule = cssRule(cardCss, '.monitor-card');
	assertDeclaration(cardRule, 'min-width', '0');
});

test('full gpu state cues use one accent with inverse available and occupied treatments', () => {
	const availableIndexRule = cssRule(cardCss, ".monitor-gpu-row[data-state='available'] .monitor-gpu-row__index");
	assert.match(availableIndexRule, /background:\s*color-mix\(in srgb, var\(--ops-card\)/);
	assertDeclaration(availableIndexRule, 'color', 'var(--ops-fg)');
	assert.doesNotMatch(availableIndexRule, /(?:^|[\n\r])\s*color:\s*var\(--ops-primary\)\s*;/);
	assertDeclaration(availableIndexRule, 'border-color', 'var(--ops-primary)');
	const occupiedIndexRule = cssRule(cardCss, ".monitor-gpu-row[data-state='occupied'] .monitor-gpu-row__index");
	assertDeclaration(occupiedIndexRule, 'background', 'var(--ops-primary)');
	assertDeclaration(occupiedIndexRule, 'border-color', 'var(--ops-primary)');
	assertDeclaration(occupiedIndexRule, 'color', 'var(--ops-on-primary)');
	assert.doesNotMatch(occupiedIndexRule, /(?:^|[\n\r])\s*color:\s*var\(--ops-primary-fg\)\s*;/);

	const utilRule = cssRule(cardCss, '.monitor-gpu-metric__fill--util');
	assertDeclaration(utilRule, 'background', 'var(--ops-primary)');
	assert.doesNotMatch(utilRule, /color-mix/);

	const memoryRule = cssRule(cardCss, '.monitor-gpu-metric__fill--memory');
	assert.match(memoryRule, /var\(--ops-primary\)/);
	assert.doesNotMatch(memoryRule, /var\(--chart-1\)|var\(--chart-2\)/);

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
	assert.match(pageSource, /<CompactDashboard[\s\S]*servers=\{\$currentServers\}[\s\S]*onOpenFull=/);
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

test('masonry action retains document-space card positions and animates later reflows', () => {
	assert.match(pageSource, /import \{[\s\S]*animateFlip[\s\S]*\} from '\$lib\/utils\/layoutFlip';/);
	assert.match(pageSource, /import \{[\s\S]*documentRect[\s\S]*\} from '\$lib\/utils\/layoutFlip';/);
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1);
	const masonryBody = pageSource.slice(masonryStart, pageSource.indexOf('\n\tfunction readTab', masonryStart));
	assert.match(masonryBody, /let previousRects = new Map<HTMLElement, FlipRect>\(\)/);
	assert.match(masonryBody, /previousRects\.get\(child\)/);
	assert.match(masonryBody, /documentRect\(child\)/);
	assert.match(masonryBody, /animateFlip\(child, previous, next/);
	assert.match(masonryBody, /previousRects = nextRects/);
	const nextRectsIndex = masonryBody.indexOf('const nextRects');
	const cancelIndex = masonryBody.indexOf('animation.cancel()');
	assert.ok(cancelIndex > nextRectsIndex, 'active FLIP animations must not be cancelled before a new non-zero layout shift is known');
	assert.match(masonryBody, /if \(moves\.length > 0\)/);
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
	assert.match(statusMarkup, /<RefreshRing/);
	assert.doesNotMatch(statusMarkup, /cycleKey=|durationMs=/);
	assert.match(statusMarkup, /\{#if refreshWarningText\(\)\}[\s\S]*ops-status-label[\s\S]*\{refreshWarningText\(\)\}/);
	assert.doesNotMatch(statusBody, /relativeTime\(|nextRefreshText\(/);
	assert.match(statusMarkup, /aria-label=\{`\$\{refreshHealthText\(\)\}[\s\S]*relativeTime\(lastRefreshAtMs\)[\s\S]*nextRefreshText\(\)/);
	assert.doesNotMatch(pageSource, /ops-refresh-cadence|refreshCadencePct/);
	assert.doesNotMatch(pageSource, /'갱신 중'|'동기화'/, 'ordinary in-flight requests must not create visible transient status copy');
});

test('header warning copy appears only after persistent refresh trouble', () => {
	assert.match(pageSource, /let refreshFailureCount = \$state\(0\)/);
	assert.match(pageSource, /const REFRESH_WARNING_FAILURE_COUNT = 2/);
	assert.match(pageSource, /refreshFailureCount = 0/);
	assert.match(pageSource, /refreshFailureCount \+= 1/);
	const warningBody = functionBody(pageSource, 'refreshWarningText');
	assert.match(warningBody, /refreshFailureCount >= REFRESH_WARNING_FAILURE_COUNT/);
	assert.match(warningBody, /nowMs - lastRefreshAtMs >= REFRESH_WARNING_AFTER_MS/);
	assert.doesNotMatch(warningBody, /refreshInFlight/);
});

test('GPU Monitor identity, refresh ring, and health label share one header row', () => {
	const identityRule = cssRule(dashboardCss, '.ops-identity');
	assertDeclaration(identityRule, 'display', 'flex');
	assertDeclaration(identityRule, 'align-items', 'center');
	const statusRule = cssRule(dashboardCss, '.ops-status');
	assertDeclaration(statusRule, 'margin', '0');
	assert.match(pageSource, /<div class="ops-identity">[\s\S]*<h1>GPU Monitor<\/h1>[\s\S]*<p[\s\S]*class="ops-status"/);
});

test('header and collapsed indicator share a continuous ten-second satellite cadence', () => {
	assert.match(
		dashboardCss,
		/@media \(min-width: 921px\)[\s\S]*?\.ops-header-inner,[\s\S]*?padding-block:\s*0\.35rem;/
	);
	assert.equal((pageSource.match(/<RefreshRing/g) ?? []).length, 2);
	assert.match(dashboardCss, /\.ops-refresh-ring__satellite\s*\{[^}]*animation:\s*ops-refresh-satellite-orbit 10s linear infinite/);
	assert.match(dashboardCss, /@keyframes ops-refresh-satellite-orbit\s*\{[\s\S]*transform:\s*rotate\(0deg\);[\s\S]*transform:\s*rotate\(360deg\);/);
	assert.match(dashboardCss, /\.ops-refresh-ring__dot\s*\{[^}]*animation:\s*ops-indicator-breathe 6(?:\.[0-9]+)?s ease-in-out infinite/);
	assert.doesNotMatch(dashboardCss, /ops-refresh-ring-orbit|--ops-refresh-duration/);
	assert.doesNotMatch(dashboardCss, /ops-refresh-cadence-flow/);
});

test('refresh requests run on a fixed cadence independent of response completion', () => {
	assert.match(pageSource, /const POLL_REFRESH_MS = 10_000/);
	assert.match(pageSource, /const POLL_REQUEST_LEAD_MS = 1_000/);
	assert.doesNotMatch(pageSource, /refreshCycleKey|refreshCycleDurationMs|scheduleRefreshRetry/);
	const startBody = functionBody(pageSource, 'startPollingCadence');
	assert.match(startBody, /schedulePollingTick\(POLL_REFRESH_MS - POLL_REQUEST_LEAD_MS\)/);
	const scheduleBody = functionBody(pageSource, 'schedulePollingTick');
	const nextSchedule = scheduleBody.indexOf('schedulePollingTick(POLL_REFRESH_MS)');
	const refreshCall = scheduleBody.indexOf('void runAutoRefresh()');
	assert.ok(nextSchedule >= 0 && refreshCall >= 0 && nextSchedule < refreshCall, 'next cadence must be scheduled before the request starts');
	const autoBody = functionBody(pageSource, 'runAutoRefresh');
	assert.match(autoBody, /if \(refreshInFlight\) return/);
	assert.match(autoBody, /await reloadDashboard\(\)/);
	assert.doesNotMatch(autoBody, /schedulePollingTick|startPollingCadence/);
});

test('page runtime is mounted and destroyed through the Svelte 5 effect lifecycle', () => {
	assert.doesNotMatch(pageSource, /import\s+\{\s*onMount\s*\}\s+from\s+'svelte'/);
	assert.match(pageSource, /\$effect\(\(\) => initPageRuntime\(\)\)/);
	assert.doesNotMatch(pageSource, /\n\s*initPageRuntime\(\);/);
	const initBody = functionBody(pageSource, 'initPageRuntime');
	assert.match(initBody, /const cleanup = \(\) =>/);
	assert.match(initBody, /runtime\.__monitoringV2PageCleanup = cleanup/);
	assert.match(initBody, /return cleanup/);
	assert.match(initBody, /if \(runtime\.__monitoringV2PageCleanup === cleanup\)/);
});

