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
	assert.match(pageSource, /\{#each \$displayServers as server \(server\.server_id\)\}/);
	assert.match(compactDashboardSource, /<CompactServerRow[\s\S]*onOpenFull=/);
	assert.doesNotMatch(compactDashboardSource, /Full에서 보기/);
	assert.match(compactServerRowSource, /onOpenFull\?: \(serverId: number\) => void/);
	assert.match(compactServerRowSource, /class="compact-row__select"[\s\S]*onclick=\{openFull\}/);
	assert.doesNotMatch(compactServerRowSource, /handleRowActivation|occupiedSlots/);
});

const dashboardCss = readFileSync(new URL('../lib/styles/monitor-dashboard.css', import.meta.url), 'utf8');
const appCss = readFileSync(new URL('../app.css', import.meta.url), 'utf8');
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
	assertDeclaration(footerRule, 'gap', '0.12rem');
	assertDeclaration(footerRule, 'padding', '0.32rem 0.7rem 0.36rem');
});


function cssRuleBody(source, selector) {
	return cssRule(source, selector).replace(/\s+/g, ' ').trim();
}

test('task 2 has no css zoom or dashboard scale wrappers in page dashboard card scope', () => {
	const scopedSources = [pageSource, dashboardCss, cardCss].join('\n');
	assert.doesNotMatch(scopedSources, /\bzoom\s*:/i);
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
	assert.match(pageSource, /const displayServers = derived\([\s\S]*\[currentServers, activeDevScenario\]/);
	assert.match(pageSource, /<CompactDashboard[\s\S]*servers=\{\$displayServers\}[\s\S]*onOpenFull=/);
	assert.match(pageSource, /\{#each \$displayServers as server \(server\.server_id\)\}/);
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

test('task 3 masonry action keeps sticky column and height-cache state', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const masonryBody = pageSource.slice(masonryStart, pageSource.indexOf('\n\tfunction readTab', masonryStart));

	assert.match(masonryBody, /let assignedColumns = new Map<HTMLElement, number>\(\)/);
	assert.match(masonryBody, /let measuredHeights = new Map<HTMLElement, number>\(\)/);
	assert.match(masonryBody, /let assignedItems: HTMLElement\[\] = \[\]/);
	assert.match(masonryBody, /let assignedColumnCount = 0/);
});

test('task 3 height-only masonry relayout reuses assigned columns and cached heights', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const layoutBody = functionBody(pageSource, 'layout', masonryStart);

	assert.match(layoutBody, /preferredColumns:\s*items\.map\(\(child\) => assignedColumns\.get\(child\) \?\? null\)/);
	assert.doesNotMatch(layoutBody, /style\.removeProperty\('grid-column-start'\)[\s\S]*getBoundingClientRect\(\)\.height/);
	assert.match(layoutBody, /measuredHeights\.get\(child\) \?\? masonryItemBorderBoxBlockSize\(child\)/);
});

test('task 3 ResizeObserver updates cached heights without structural invalidation', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const masonryBody = pageSource.slice(masonryStart, pageSource.indexOf('\n\tfunction readTab', masonryStart));

	assert.match(masonryBody, /new ResizeObserver\(\(entries\) => \{[\s\S]*measuredHeights\.set\([\s\S]*schedule\(\)/);
	assert.doesNotMatch(masonryBody, /new ResizeObserver\(\(entries\)[\s\S]*assignedColumns\.clear\(\)/);
});

test('task 3 structural masonry changes clear assignments so layout can rebalance', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const masonryBody = pageSource.slice(masonryStart, pageSource.indexOf('\n\tfunction readTab', masonryStart));

	assert.match(masonryBody, /assignedItems/);
	assert.match(masonryBody, /assignedColumnCount/);
	assert.match(masonryBody, /assignedColumns\.clear\(\)/);
	assert.match(masonryBody, /currentColumnCount !== assignedColumnCount|assignedColumnCount !== currentColumnCount/);
	assert.match(masonryBody, /items\.length !== assignedItems\.length|assignedItems\.length !== items\.length/);
	assert.match(masonryBody, /items\.some\(\(child, index\) => child !== assignedItems\[index\]\)/);
});


test('task 3 column-count structural change clears height cache before masonry placement', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const layoutBody = functionBody(pageSource, 'layout', masonryStart);

	const columnChangedIndex = layoutBody.indexOf('const columnCountChanged = currentColumnCount !== assignedColumnCount');
	assert.notEqual(columnChangedIndex, -1, 'column-count changes must be tracked separately from ordinary structure changes');
	const heightClearIndex = layoutBody.indexOf('measuredHeights.clear()', columnChangedIndex);
	const spansIndex = layoutBody.indexOf('const spans = items.map', columnChangedIndex);
	assert.ok(heightClearIndex > columnChangedIndex, 'column-count changes must invalidate cached heights');
	assert.ok(spansIndex > heightClearIndex, 'height cache must be invalidated before computing spans/assignments');
});

test('task 3 cold and ResizeObserver height cache share one border-box measurement helper', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const masonryBody = pageSource.slice(masonryStart, pageSource.indexOf('\n\tfunction readTab', masonryStart));
	const layoutBody = functionBody(pageSource, 'layout', masonryStart);

	assert.match(masonryBody, /function masonryItemBorderBoxBlockSize\(child: HTMLElement, entry\?: ResizeObserverEntry\): number/);
	assert.match(masonryBody, /borderBoxSize/);
	assert.match(masonryBody, /blockSize/);
	assert.match(layoutBody, /measuredHeights\.get\(child\) \?\? masonryItemBorderBoxBlockSize\(child\)/);
	assert.match(masonryBody, /measuredHeights\.set\(entry\.target, masonryItemBorderBoxBlockSize\(entry\.target, entry\)\)/);
	assert.doesNotMatch(masonryBody, /entry\.contentRect\.height/);
});

test('task 3 masonry keeps DOM order and server order stable', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const masonryBody = pageSource.slice(masonryStart, pageSource.indexOf('\n\tfunction readTab', masonryStart));

	assert.match(pageSource, /\{#each \$displayServers as server \(server\.server_id\)\}/);
	assert.match(pageSource, /role="list"[\s\S]*role="listitem"/);
	assert.doesNotMatch(masonryBody, /appendChild|insertBefore|replaceChildren|sort\(/);
});

test('task 3 masonry FLIP uses active visual rects and cancels after next layout is known', () => {
	const masonryStart = pageSource.indexOf('function masonry');
	assert.notEqual(masonryStart, -1, 'Missing masonry action');
	const masonryBody = pageSource.slice(masonryStart, pageSource.indexOf('\n\tfunction readTab', masonryStart));

	assert.match(masonryBody, /layoutAnimations\.has\(child\) \? visualRects\.get\(child\) \?\? previousFinal : previousFinal/);
	assert.ok(masonryBody.indexOf('const nextRects') < masonryBody.indexOf('animation.cancel()'));
});

test('persistent header shows semantic health and cadence without visible second-by-second copy', () => {
	const statusClass = pageSource.indexOf('class="ops-status"');
	assert.notEqual(statusClass, -1, 'Missing persistent header status');
	const statusStart = pageSource.lastIndexOf('<p', statusClass);
	const statusEnd = pageSource.indexOf('</p>', statusClass);
	assert.notEqual(statusEnd, -1, 'Missing persistent header status closing tag');
	const statusMarkup = pageSource.slice(statusStart, statusEnd);
	const statusBody = statusMarkup.slice(statusMarkup.indexOf('>') + 1);

	assert.match(statusMarkup, /aria-label=\{refreshIssueText\(\) \|\| '정상'\}/);
	assert.match(statusMarkup, /<RefreshRing/);
	assert.doesNotMatch(statusMarkup, /cycleKey=|durationMs=/);
	assert.match(statusMarkup, /\{#if refreshWarningText\(\)\}[\s\S]*ops-status-label[\s\S]*\{refreshWarningText\(\)\}/);
	assert.doesNotMatch(statusBody, /relativeTime\(|nextRefreshText\(/);
	assert.doesNotMatch(statusMarkup, /relativeTime\(lastRefreshAtMs\)|nextRefreshText\(\)/);
	assert.doesNotMatch(pageSource, /function nextRefreshText/);
	assert.doesNotMatch(pageSource, /class=\"ops-indicator-status\">\{refreshHealthText\(\)\}[\s\S]*relativeTime\(lastRefreshAtMs\)/);
	assert.match(pageSource, /function refreshIssueText[\s\S]*warning \? \x60\$\{warning\} · \$\{relativeTime\(lastRefreshAtMs\)\}\x60 : ''/);
	assert.match(pageSource, /\{#if refreshWarningText\(\)\}[\s\S]*ops-indicator-status[\s\S]*\{refreshIssueText\(\)\}[\s\S]*\{\/if\}/);
	assert.doesNotMatch(pageSource, /ops-refresh-cadence|refreshCadencePct/);
	assert.doesNotMatch(pageSource, /'갱신 중'|'동기화'/, 'ordinary in-flight requests must not create visible transient status copy');
});

test('collapsed indicator is fixed and reserves no dashboard layout lane', () => {
	assert.doesNotMatch(pageSource, /headerIndicatorLane|resolveIndicatorLaneHeight|shouldSyncIndicatorLane/);
	assert.doesNotMatch(pageSource, /indicatorLaneHeightPx|syncIndicatorLaneAfterDom|scheduleIndicatorLaneSync/);
	assert.doesNotMatch(pageSource, /--ops-indicator-lane-height/);
	assert.doesNotMatch(pageSource, /class:ops-page-compact|class:ops-page-indicator-panel-open/);
	assert.doesNotMatch(pageSource, /bind:this=\{indicatorTriggerElement\}|bind:this=\{indicatorPanelElement\}/);
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
		/@media \(min-width: 921px\)[\s\S]*?\.ops-header-inner\s*\{[\s\S]*?padding-block:\s*0\.35rem;/
	);
	assert.equal((pageSource.match(/<RefreshRing/g) ?? []).length, 2);
	assert.match(dashboardCss, /\.ops-refresh-ring__satellite\s*\{[^}]*animation:\s*ops-refresh-satellite-orbit 10s linear infinite/);
	assert.match(dashboardCss, /@keyframes ops-refresh-satellite-orbit\s*\{[\s\S]*transform:\s*rotate\(0deg\);[\s\S]*transform:\s*rotate\(360deg\);/);
	assert.match(dashboardCss, /\.ops-refresh-ring__dot\s*\{[^}]*animation:\s*ops-indicator-breathe 6s ease-in-out infinite/);
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
	assert.match(pageSource, /\$effect\(\(\) => untrack\(\(\) => initPageRuntime\(\)\)\)/);
	assert.doesNotMatch(pageSource, /\n\s*initPageRuntime\(\);/);
	const initBody = functionBody(pageSource, 'initPageRuntime');
	assert.match(initBody, /const cleanup = \(\) =>/);
	assert.match(initBody, /runtime\.__monitoringV2PageCleanup = cleanup/);
	assert.match(initBody, /return cleanup/);
	assert.match(initBody, /if \(runtime\.__monitoringV2PageCleanup === cleanup\)/);
});


test('page runtime init is untracked so header visibility changes cannot remount and cancel handoff RAF', () => {
	assert.match(pageSource, /import \{\s*tick,\s*untrack\s*\} from 'svelte';/);
	assert.match(pageSource, /\$effect\(\(\) => untrack\(\(\) => initPageRuntime\(\)\)\)/);
	const initBody = functionBody(pageSource, 'initPageRuntime');
	assert.match(initBody, /headerPreviousY = currentHeaderScrollPosition\(\)/);
	assert.match(initBody, /runtime\.__monitoringV2PageCleanup\?\.\(\)/);
	assert.match(pageSource, /scheduleHeaderIndicatorHandoff\('collapse'\)/);
	assert.match(pageSource, /cleanupHeaderIndicatorHandoff\(\)/);
});


test('View trigger names the current Full or Compact mode before the menu opens', () => {
	const triggerStart = pageSource.indexOf('class="ops-utility-action"');
	assert.notEqual(triggerStart, -1);
	const triggerEnd = pageSource.indexOf('</button>', triggerStart);
	const triggerMarkup = pageSource.slice(triggerStart, triggerEnd);
	assert.ok(triggerMarkup.includes('{dashboardViewLabel($dashboardView)}'));
	assert.ok(!triggerMarkup.includes('>보기 '));
});

test('dashboard keyboard shortcuts share the safe animated theme action path', () => {
	assert.match(pageSource, /resolveDashboardShortcut/);
	assert.match(pageSource, /case 'toggle-view'/);
	assert.match(pageSource, /setDashboardView\(\$dashboardView === 'compact' \? 'default' : 'compact'\)/);
	assert.match(pageSource, /case 'select-network'/);
	assert.match(pageSource, /selectNetwork\(shortcut\.tab\)/);
	assert.match(pageSource, /case 'toggle-theme'/);
	const themeCase = pageSource.slice(pageSource.indexOf("case 'toggle-theme'"), pageSource.indexOf('break;', pageSource.indexOf("case 'toggle-theme'")));
	assert.match(themeCase, /void runThemeModeReveal\(themeModeButtonElement[\s\S]*\)/);
	assert.doesNotMatch(themeCase, /toggleThemeMode\(\)|setThemeMode\(/);
});

test('Full and Compact changes use a restrained keyed transition', () => {
	assert.match(pageSource, /import \{ fly \} from 'svelte\/transition';/);
	assert.match(pageSource, /\{#key \$dashboardView\}/);
	assert.match(pageSource, /class="ops-dashboard-view-stage"/);
	assert.match(pageSource, /in:fly=\{dashboardViewTransition\}/);
	assert.match(pageSource, /prefers-reduced-motion/);
});

test('Task 5 View menu exposes material presets instead of accent color swatches', () => {
	assert.match(pageSource, /materialThemeOptions/);
	assert.match(pageSource, /setMaterialTheme/);
	assert.doesNotMatch(pageSource, /colorTheme|setColorTheme|colorThemeOptions/);
	assert.match(pageSource, /Theme \/ Material/);
	assert.match(pageSource, /ops-material-options/);
	assert.match(pageSource, /ops-material-tile/);
	assert.match(pageSource, /data-material-preview=\{option\.value\}/);
	assert.match(pageSource, /setMaterialTheme\(option\.value\);\s*viewMenuOpen = false;/);
	assert.doesNotMatch(pageSource, /--swatch|ops-color-options|색상 테마/);
});

test('Task 5 functional layers use material variables while cards remain mostly opaque', () => {
	for (const selector of ['.ops-header', '.ops-mode-action']) {
		const rule = cssRule(dashboardCss, selector);
		assert.match(rule, /--material-|backdrop-filter/, `${selector} should consume material variables`);
	}
	assert.match(
		dashboardCss,
		/\.ops-view-menu,\s*\.ops-overflow-menu,\s*\.ops-indicator-panel\s*\{[\s\S]*var\(--material-blur\)[\s\S]*var\(--material-shadow\)/,
		'header menus and indicator panel share functional material variables'
	);
	assert.doesNotMatch(cssRule(cardCss, '.monitor-card'), /--material-/, 'server card shell stays dense and mostly opaque');
	assert.match(cardCss, /monitor-card__body[\s\S]*backdrop-filter/, 'Task 5 allows body-scoped material blur for failure veil only');
	assert.match(cardCss, /\.monitor-card__state-veil[\s\S]*--material-blur/, 'Task 6 scopes material blur consumption to the card veil');
});


test('Task 6 indicator panel remains mounted and uses accessible hidden-state semantics', () => {
	assert.match(pageSource, /id=\{indicatorPanelId\}[\s\S]*class="ops-indicator-panel"/);
	assert.match(pageSource, /aria-hidden=\{!indicatorPanelOpen\}/);
	assert.match(pageSource, /inert=\{!indicatorPanelOpen\}/);
	assert.doesNotMatch(pageSource, /\{#if indicatorPanelOpen\}[\s\S]*ops-indicator-panel/);
	const panelRuleMatch = dashboardCss.match(/\.ops-indicator-panel\s*\{[\s\S]*?transform:\s*translate3d[\s\S]*?opacity:\s*0;[\s\S]*?\n\}/);
	assert.ok(panelRuleMatch, 'Missing closed indicator panel CSS rule');
	const panelRule = panelRuleMatch[0];
	assert.doesNotMatch(panelRule, /display\s*:\s*none/);
	assert.match(panelRule, /opacity\s*:\s*0/);
	assert.match(panelRule, /visibility\s*:\s*hidden/);
	assert.match(panelRule, /pointer-events\s*:\s*none/);
	assert.match(panelRule, /transform\s*:\s*translate3d\([^)]*\)\s*scale\(/);
	assert.match(panelRule, /transition:[^}]*opacity\s+2(?:4|5|6|7|8)0ms[^}]*transform\s+2(?:4|5|6|7|8)0ms[^}]*visibility\s+0s\s+linear\s+2(?:4|5|6|7|8)0ms/s);
	const openRule = cssRule(dashboardCss, '.ops-indicator-panel.ops-indicator-panel-open');
	assert.match(openRule, /opacity\s*:\s*1/);
	assert.match(openRule, /visibility\s*:\s*visible/);
	assert.match(openRule, /pointer-events\s*:\s*auto/);
	assert.match(openRule, /transform\s*:\s*translate3d\(0, 0, 0\)\s*scale\(1\)/);
});

test('Task 6 header and fixed indicator rings are bound and FLIP handoff runs both directions with cleanup', () => {
	assert.match(pageSource, /bind:this=\{headerRefreshRingElement\}[\s\S]*<RefreshRing[^>]*variant="header"/);
	assert.match(pageSource, /bind:this=\{fixedRefreshRingElement\}[\s\S]*<RefreshRing[^>]*variant="floating"/);
	assert.match(pageSource, /let headerIndicatorHandoffAnimation = \$state<Animation \| null>\(null\)/);
	assert.match(pageSource, /let headerIndicatorHandoffFrame: number \| null = null/);
	assert.match(pageSource, /function scheduleHeaderIndicatorHandoff/);
	assert.match(pageSource, /function runHeaderIndicatorHandoff/);
	assert.match(pageSource, /function cleanupHeaderIndicatorHandoff/);
	assert.match(pageSource, /headerIndicatorHandoffAnimation\?\.cancel\(\)/);
	assert.match(pageSource, /window\.matchMedia\('\(prefers-reduced-motion: reduce\)'\)\.matches/);
	assert.match(pageSource, /await tick\(\)/);
	assert.match(pageSource, /getBoundingClientRect\(\)/);
	assert.match(pageSource, /animate\(\[\s*\{[^}]*transform: 'translate3d\(0, 0, 0\) scale\(1, 1\)'[\s\S]*transform: `translate3d\(\$\{deltaX\}px, \$\{deltaY\}px, 0\) scale\(\$\{scaleX\}, \$\{scaleY\}\)`/s);
	assert.match(pageSource, /scheduleHeaderIndicatorHandoff\('collapse'\)/);
	assert.match(pageSource, /scheduleHeaderIndicatorHandoff\('reveal'\)/);
	const cleanupBody = functionBody(pageSource, 'cleanupHeaderIndicatorHandoff');
	assert.match(cleanupBody, /cancelAnimationFrame/);
	assert.match(cleanupBody, /headerIndicatorHandoffAnimation\?\.cancel\(\)/);
	assert.match(cleanupBody, /remove\(\)/);
});

test('Task 6 theme reveal is button-centered, locked, destination-token based, and cleans async work', () => {
	assert.match(pageSource, /import \{[\s\S]*setThemeMode[\s\S]*type ThemeMode[\s\S]*\} from '\$lib\/stores\/theme';/);
	assert.doesNotMatch(pageSource, /toggleThemeMode/);
	assert.match(pageSource, /let themeModeButtonElement = \$state<HTMLButtonElement \| null>\(null\)/);
	assert.match(pageSource, /let themeRevealLocked = \$state\(false\)/);
	assert.match(pageSource, /let themeRevealOverlay: HTMLDivElement \| null = null/);
	assert.doesNotMatch(pageSource, /themeRevealTimers/);
	assert.match(pageSource, /type ThemeRevealOrigin = \{ x: number; y: number \}/);
	assert.match(pageSource, /function readVisibleThemeButtonCenter/);
	assert.match(pageSource, /function fallbackThemeRevealCenter/);
	assert.match(pageSource, /function farthestCornerRadius/);
	assert.match(pageSource, /function runThemeModeReveal/);
	const revealBody = functionBody(pageSource, 'runThemeModeReveal');
	assert.match(revealBody, /readVisibleThemeButtonCenter\(originElement\)/);
	assert.match(revealBody, /const originX = origin\.x/);
	assert.match(revealBody, /const originY = origin\.y/);
	assert.match(revealBody, /farthestCornerRadius\(originX, originY\)/);
	assert.match(revealBody, /themeRevealLocked = true/);
	assert.match(revealBody, /data-theme-mode/);
	assert.match(revealBody, /data-material/);
	assert.match(revealBody, /theme-mode-reveal__edge/);
	assert.match(revealBody, /clipPath:\s*`circle\(0px at \$\{originX\}px \$\{originY\}px\)`/);
	assert.match(revealBody, /duration:\s*480/);
	assert.match(revealBody, /await animation\.finished/);
	assert.match(revealBody, /setThemeMode\(nextMode\)/);
	assert.match(revealBody, /if \(shouldRestoreFocus && visibleOrigin && originElement\) originElement\.focus\(\{ preventScroll: true \}\)/);
	assert.match(revealBody, /cleanupThemeReveal\(\)/);
	assert.match(pageSource, /function cleanupThemeReveal/);
	const cleanupBody = functionBody(pageSource, 'cleanupThemeReveal');
	assert.doesNotMatch(cleanupBody, /clearTimeout/);
	assert.doesNotMatch(cleanupBody, /cancelAnimationFrame/);
	assert.match(cleanupBody, /themeRevealAnimation\?\.cancel\(\)/);
	assert.match(cleanupBody, /themeRevealOverlay\?\.remove\(\)/);
});

test('Task 6 theme reveal reduced-motion path applies mode immediately and preserves focus', () => {
	const revealBody = functionBody(pageSource, 'runThemeModeReveal');
	assert.match(revealBody, /prefers-reduced-motion: reduce/);
	assert.match(revealBody, /if \(reducedMotion\) \{[\s\S]*setThemeMode\(nextMode\)[\s\S]*if \(shouldRestoreFocus && visibleOrigin && originElement\) originElement\.focus\(\{ preventScroll: true \}\)[\s\S]*return;[\s\S]*\}/);
});

test('Task 6 mode button uses inline SVG sun and moon icons instead of glyph characters', () => {
	const buttonStart = pageSource.indexOf('class="ops-mode-action"');
	assert.notEqual(buttonStart, -1);
	const buttonEnd = pageSource.indexOf('</button>', buttonStart);
	const buttonMarkup = pageSource.slice(buttonStart, buttonEnd);
	assert.match(buttonMarkup, /<svg[\s\S]*aria-hidden="true"/);
	assert.match(buttonMarkup, /ops-mode-icon--sun/);
	assert.match(buttonMarkup, /ops-mode-icon--moon/);
	assert.doesNotMatch(buttonMarkup, /☀|☾|☽|🌙|🌞/);
});

test('Task 6 reveal overlay resolves destination theme tokens through app css, not page literals', () => {
	assert.match(appCss, /\.theme-mode-reveal\[data-theme-mode='dark'\]/);
	assert.match(appCss, /\.theme-mode-reveal\[data-theme-mode='light'\]/);
	assert.match(appCss, /\.theme-mode-reveal\[data-material='claude'\]/);
	assert.match(appCss, /\.theme-mode-reveal\[data-material='astro'\]/);
	const revealRule = cssRule(appCss, '.theme-mode-reveal');
	assert.match(revealRule, /z-index:\s*8[0-9]\s*;/);
	assert.match(revealRule, /background:\s*var\(--ops-bg\)/);
	assert.match(appCss, /\.theme-mode-reveal__edge\s*\{[\s\S]*border:[\s\S]*color-mix/);
	const revealBody = functionBody(pageSource, 'runThemeModeReveal');
	assert.doesNotMatch(revealBody, /#[0-9a-fA-F]{3,8}/);
});


test('Task 6 mobile indicator panel keeps a closed translate-scale transition', () => {
	const mobile = dashboardCss.match(/@media \(max-width: 640px\) \{[\s\S]*?\n\}/)?.[0] ?? '';
	assert.match(mobile, /\.ops-indicator-panel\s*\{[\s\S]*transform:\s*translate3d\([^)]*\)\s*scale\(/);
	assert.doesNotMatch(mobile, /\.ops-indicator-panel\s*\{[\s\S]*transform:\s*none/);
});

test('Task 6 reveal cleanup releases lock after animation rejection without applying mode', () => {
	const revealBody = functionBody(pageSource, 'runThemeModeReveal');
	assert.match(revealBody, /let covered = false/);
	assert.match(revealBody, /catch \{[\s\S]*cleanupThemeReveal\(\)[\s\S]*return;[\s\S]*\}/);
	const catchStart = revealBody.indexOf('catch {');
	const catchEnd = revealBody.indexOf('}', catchStart);
	const catchBody = revealBody.slice(catchStart, catchEnd);
	assert.doesNotMatch(catchBody, /setThemeMode/);
	assert.match(revealBody, /covered = true;[\s\S]*setThemeMode\(nextMode\)/);
});

test('Task 6 shortcut theme reveal uses cached or logical origin instead of focusing a hidden header button', () => {
	const shortcutStart = pageSource.indexOf("case 'toggle-theme'");
	const themeCase = pageSource.slice(shortcutStart, pageSource.indexOf('break;', shortcutStart));
	assert.match(themeCase, /void runThemeModeReveal\(themeModeButtonElement, null, false\)/);
	assert.doesNotMatch(themeCase, /fallbackThemeRevealCenter\(\)/);
	assert.doesNotMatch(themeCase, /themeModeButtonElement\.getBoundingClientRect\(\)/);
});


test('Task 6 reveal overlay covers header while a body-level toggle proxy stays above it', () => {
	const revealRule = cssRule(appCss, '.theme-mode-reveal');
	assert.match(revealRule, /z-index:\s*8[0-9]\s*;/);
	assert.doesNotMatch(appCss, /(?:^|\n)\.theme-mode-toggle-proxy\s*\{[\s\S]*position:\s*fixed/);
	const proxyRule = cssRule(appCss, 'body > .theme-mode-toggle-proxy.ops-mode-action');
	assert.match(proxyRule, /position:\s*fixed\s*;/);
	assert.match(proxyRule, /z-index:\s*9[0-9]\s*;/);
	const revealBody = functionBody(pageSource, 'runThemeModeReveal');
	assert.match(revealBody, /const toggleProxy = createThemeToggleProxy\(originElement\)/);
	assert.match(pageSource, /themeRevealToggleProxy\?\.remove\(\)/);
	const proxyBody = functionBody(pageSource, 'createThemeToggleProxy');
	assert.match(proxyBody, /originElement\.cloneNode\(true\)/);
	assert.match(proxyBody, /proxy\.style\.left = `\$\{rect\.left\}px`/);
	assert.match(proxyBody, /proxy\.style\.top = `\$\{rect\.top\}px`/);
	assert.match(proxyBody, /proxy\.style\.width = `\$\{rect\.width\}px`/);
	assert.match(proxyBody, /proxy\.style\.height = `\$\{rect\.height\}px`/);
	assert.match(proxyBody, /document\.body\.appendChild\(proxy\)/);
});

test('Task 6 C shortcut uses exact visible button center and cached measured center only when hidden', () => {
	const shortcutStart = pageSource.indexOf("case 'toggle-theme'");
	const themeCase = pageSource.slice(shortcutStart, pageSource.indexOf('break;', shortcutStart));
	assert.match(themeCase, /void runThemeModeReveal\(themeModeButtonElement, null, false\)/);
	assert.doesNotMatch(themeCase, /fallbackThemeRevealCenter\(\)/);
	const visibleCenterBody = functionBody(pageSource, 'readVisibleThemeButtonCenter');
	assert.match(visibleCenterBody, /originElement\.getBoundingClientRect\(\)/);
	assert.match(visibleCenterBody, /lastThemeModeButtonCenter = center/);
	assert.match(pageSource, /cacheVisibleThemeButtonCenter\(\)/);
	const initBody = functionBody(pageSource, 'initPageRuntime');
	assert.match(initBody, /let themeButtonCacheFrame: number \| null = requestAnimationFrame\(cacheVisibleThemeButtonCenter\)/);
	assert.match(initBody, /if \(themeButtonCacheFrame !== null\) \{\s*cancelAnimationFrame\(themeButtonCacheFrame\);\s*themeButtonCacheFrame = null;\s*\}/);
	const cleanupBody = initBody.slice(initBody.indexOf('const cleanup = () =>'));
	assert.doesNotMatch(cleanupBody, /requestAnimationFrame\(cacheVisibleThemeButtonCenter\)/);
	assert.match(pageSource, /function fallbackThemeRevealCenter[\s\S]*lastThemeModeButtonCenter/);
});


test('Task 6 hidden compact header mode control is not a visible reveal source or proxy source', () => {
	assert.match(pageSource, /function isVisibleThemeRevealSource/);
	const visibilityBody = functionBody(pageSource, 'isVisibleThemeRevealSource');
	assert.match(visibilityBody, /originElement\.closest\('\.ops-header-compact'\)/);
	assert.match(visibilityBody, /return false/);
	const visibleCenterBody = functionBody(pageSource, 'readVisibleThemeButtonCenter');
	assert.match(visibleCenterBody, /isVisibleThemeRevealSource\(originElement\)/);
	assert.match(visibleCenterBody, /return null/);
	const proxyBody = functionBody(pageSource, 'createThemeToggleProxy');
	assert.match(proxyBody, /isVisibleThemeRevealSource\(originElement\)/);
	assert.match(pageSource, /const visibleOrigin = readVisibleThemeButtonCenter\(originElement\);\s*const origin = originOverride \?\? visibleOrigin \?\? fallbackThemeRevealCenter\(\)/);
});


test('Task 6 stacking keeps refresh handoff below theme reveal while toggle proxy stays above', () => {
	const handoffRule = cssRule(dashboardCss, '.ops-refresh-handoff');
	const revealRule = cssRule(appCss, '.theme-mode-reveal');
	const proxyRule = cssRule(appCss, 'body > .theme-mode-toggle-proxy.ops-mode-action');
	assert.match(handoffRule, /z-index:\s*83\s*;/);
	assert.match(revealRule, /z-index:\s*84\s*;/);
	assert.match(proxyRule, /z-index:\s*94\s*;/);
});

test('Task 6 FLIP handoff suppresses live source and target ring visuals until cleanup', () => {
	assert.match(pageSource, /let headerIndicatorHandoffActive = \$state\(false\)/);
	assert.match(pageSource, /headerIndicatorHandoffActive = true/);
	const cleanupBody = functionBody(pageSource, 'cleanupHeaderIndicatorHandoff');
	assert.match(cleanupBody, /headerIndicatorHandoffActive = false/);
	assert.match(pageSource, /class:ops-refresh-ring-wrap--handoff-active=\{headerIndicatorHandoffActive\}/);
	assert.match(dashboardCss, /\.ops-refresh-ring-wrap--handoff-active\s*\{[\s\S]*opacity:\s*0/);
});


test('Task 4 failure veil reveals card body and fully hides veil on hover or focus', () => {
	const bodyRule = cssRule(cardCss, ".monitor-card[data-operational-state='impaired'] .monitor-card__body");
	assert.match(bodyRule, /filter:\s*blur\((?:1(?:\.\d+)?|1\.5)px\)/, 'default body blur should stay light, around 1-1.5px');
	assert.match(bodyRule, /opacity:\s*0\.(?:6|7|8)\d*\s*;/, 'default body should remain readable around 0.7 opacity');

	const hoverRule = cssRule(cardCss, ".monitor-card[data-operational-state='impaired']:is(:hover, :focus-within) .monitor-card__body");
	assert.match(hoverRule, /filter:\s*none\s*;/);
	assert.match(hoverRule, /opacity:\s*1\s*;/);

	const veilRule = cssRule(cardCss, '.monitor-card__state-veil');
	assert.match(veilRule, /pointer-events:\s*none\s*;/);
	assert.match(veilRule, /backdrop-filter:\s*blur\(/);
	assert.match(cardCss, /monitor-card__state-veil-label/);
	assert.match(cardCss, /monitor-card__state-veil-secondary/);

	const revealVeilRule = cssRule(cardCss, ".monitor-card[data-operational-state='impaired']:is(:hover, :focus-within) .monitor-card__state-veil");
	assert.match(revealVeilRule, /opacity:\s*0\s*;/);
	assert.match(revealVeilRule, /visibility:\s*hidden\s*;/);
	assert.match(revealVeilRule, /pointer-events:\s*none\s*;/);
	assert.match(revealVeilRule, /backdrop-filter:\s*none\s*;/);
	assert.doesNotMatch(cardCss, /\.monitor-card__header[^{]*filter:\s*blur/, 'header must stay unblurred');
});

test('Task 5 failure veil CSS has immediate reduced-motion transitions', () => {
	const reduceStart = cardCss.indexOf('@media (prefers-reduced-motion: reduce)');
	assert.notEqual(reduceStart, -1, 'missing reduced-motion media query');
	const reduceCss = cardCss.slice(reduceStart);
	assert.match(reduceCss, /\.monitor-card__body/);
	assert.match(reduceCss, /\.monitor-card__state-veil/);
	assert.match(reduceCss, /transition:\s*none\s*;/);
});


test('Task 6 exposes shortcut discovery with menu legend and pointer-safe tooltips', () => {
	assert.match(pageSource, /Theme \/ Material/);
	assert.doesNotMatch(pageSource, /<span class="ops-menu-label">재질<\/span>/);
	assert.match(pageSource, /V 보기 · 1\/2\/3 망 · C 명암/);

	assert.match(pageSource, /shortcut: '1'[\s\S]*tooltip: '1 내부망'/);
	assert.match(pageSource, /shortcut: '2'[\s\S]*tooltip: '2 외부망'/);
	assert.match(pageSource, /shortcut: '3'[\s\S]*tooltip: '3 전체망'/);
	assert.match(pageSource, /aria-keyshortcuts=\{tab\.shortcut\}/);
	assert.match(pageSource, /data-shortcut-tooltip=\{tab\.tooltip\}/);
	assert.match(pageSource, /class="ops-indicator-network"[\s\S]*aria-keyshortcuts=\{tab\.shortcut\}/);
	assert.match(pageSource, /class="ops-network ops-network-desktop"[\s\S]*aria-keyshortcuts=\{tab\.shortcut\}/);
	assert.match(pageSource, /class="ops-mode-action"[\s\S]*aria-keyshortcuts="C"[\s\S]*data-shortcut-tooltip="C 명암"/);
	assert.match(pageSource, /class="ops-utility-action"[\s\S]*aria-keyshortcuts="V"[\s\S]*data-shortcut-tooltip="V 보기"/);

	assert.match(dashboardCss, /\[data-shortcut-tooltip\]::after/);
	assert.match(dashboardCss, /pointer-events:\s*none;/);
	assert.match(dashboardCss, /\[data-shortcut-tooltip\]:is\(:hover, :focus-visible\)::after/);
	assert.match(dashboardCss, /content:\s*attr\(data-shortcut-tooltip\);/);
	assert.match(dashboardCss, /\.ops-menu-shortcut-legend/);
});

test('Task 6 central material variables are consumed by dashboard, menu, veil, and controls', () => {
	assert.match(dashboardCss, /\.ops-header-shell[\s\S]*var\(--material-veil-mix\)/);
	assert.match(dashboardCss, /\.ops-header[\s\S]*var\(--material-veil-mix\)/);
	assert.match(dashboardCss, /\.ops-view-menu,[\s\S]*\.ops-indicator-panel[\s\S]*var\(--material-veil-mix\)/);
	assert.match(dashboardCss, /\.ops-network button,[\s\S]*\.monitor-dashboard-button[\s\S]*var\(--material-control-mix\)/);
	assert.match(dashboardCss, /\.monitor-dashboard-state[\s\S]*var\(--material-card-mix\)/);
	assert.doesNotMatch(dashboardCss, /\.monitor-dashboard-state[\s\S]*box-shadow:\s*var\(--ops-shadow\)/);
	assert.match(cardCss, /\.monitor-card__state-veil[\s\S]*var\(--material-veil-mix\)/);
	assert.match(cardCss, /\.monitor-card__state-veil[\s\S]*var\(--material-blur\)/);
});


test('Task 4 full cards receive the page shared visibility-aware nowMs clock', () => {
	assert.match(pageSource, /let nowMs = \$state\(Date\.now\(\)\)/);
	assert.match(pageSource, /function currentTickIntervalMs\(\): number[\s\S]*document\.visibilityState === 'hidden' \? HIDDEN_TICK_MS : VISIBLE_TICK_MS/);
	assert.match(pageSource, /ticker = setInterval\(\(\) => \{[\s\S]*nowMs = Date\.now\(\)[\s\S]*\}, currentTickIntervalMs\(\)\)/);
	assert.match(pageSource, /<ServerCard\s+\{server\}\s+\{nowMs\}[\s\S]*onEdit=\{handleEditServer\}[\s\S]*showNetwork=\{\$activeTab === 'all'\}/);
});

test('Task 4 shared card motion uses the local settle token without height or top animation', () => {
	const cardRule = cssRule(cardCss, '.monitor-card');
	assertDeclaration(cardRule, '--monitor-card-settle-duration', '260ms');
	assertDeclaration(cardRule, '--monitor-card-settle-easing', 'cubic-bezier(0.22, 1, 0.36, 1)');
	assertDeclaration(cardRule, '--monitor-card-settle', 'var(--monitor-card-settle-duration) var(--monitor-card-settle-easing)');
	assert.match(cardRule, /transition:[^;]*transform\s+var\(--monitor-card-settle\)[^;]*box-shadow\s+var\(--monitor-card-settle\)[^;]*border-color\s+var\(--monitor-card-settle\)/s);

	const availabilityRailRule = cssRule(cardCss, '.monitor-card::before');
	assert.match(availabilityRailRule, /transition:[^;]*opacity\s+var\(--monitor-card-settle\)[^;]*transform\s+var\(--monitor-card-settle\)/s);

	const bodyRule = cssRule(cardCss, '.monitor-card__body');
	assert.match(bodyRule, /transition:[^;]*filter\s+var\(--monitor-card-settle\)[^;]*opacity\s+var\(--monitor-card-settle\)[^;]*backdrop-filter\s+var\(--monitor-card-settle\)/s);

	const veilRule = cssRule(cardCss, '.monitor-card__state-veil');
	assert.match(veilRule, /transition:[^;]*opacity\s+var\(--monitor-card-settle\)[^;]*visibility\s+0s\s+linear\s+var\(--monitor-card-settle-duration\)[^;]*backdrop-filter\s+var\(--monitor-card-settle\)/s);

	const meterRule = cssRule(cardCss, '.monitor-gpu-metric__fill,\n.monitor-meter__fill');
	assert.match(meterRule, /transition:\s*width\s+var\(--monitor-card-settle\)/);

	const disclosureRule = cssRule(cardCss, '.monitor-card__disclosure-shell');
	assert.match(disclosureRule, /transition:[^;]*grid-template-rows\s+0s\s+linear\s+var\(--monitor-card-settle-duration\)[^;]*opacity\s+var\(--monitor-card-settle\)[^;]*transform\s+var\(--monitor-card-settle\)[^;]*visibility\s+0s\s+linear\s+var\(--monitor-card-settle-duration\)[^;]*pointer-events\s+0s\s+linear\s+var\(--monitor-card-settle-duration\)/s);
	assert.doesNotMatch(cardCss, /transition[^;]*(?:height|top)/, 'do not animate height/top directly');
});

test('Task 4 disclosure chevron uses the same settle timing as the disclosure shell', () => {
	const chevronRule = cssRule(cardCss, '.monitor-card__footer-disclosure');
	assert.match(chevronRule, /transition:[^;]*transform\s+var\(--monitor-card-settle\)[^;]*border-color\s+var\(--monitor-card-settle\)/s);
	assert.doesNotMatch(chevronRule, /160ms|ease(?:[,;]|$)/, 'chevron must not keep its pre-Task4 160ms ease motion');
});

test('Task 4 reduced-motion disables card, rail, chevron, shell, and meter transitions', () => {
	const reduced = cardCss.slice(cardCss.indexOf('@media (prefers-reduced-motion: reduce)'));
	assert.match(reduced, /\.monitor-card::before[\s\S]*transition:\s*none/);
	assert.match(reduced, /\.monitor-card__footer-disclosure[\s\S]*transition:\s*none/);
	assert.match(reduced, /\.monitor-card__disclosure-shell[\s\S]*transition:\s*none/);
	assert.match(reduced, /\.monitor-gpu-metric__fill[\s\S]*transition:\s*none/);
});

test('Task 4 compact indicator open close uses the same 240-280ms settling motion and reduced-motion disables it', () => {
	const panelRule = dashboardCss.match(new RegExp('\\.ops-indicator-panel\\s*\\{[\\s\\S]*?transform:\\s*translate3d[\\s\\S]*?transition:[\\s\\S]*?\\n\\}'))?.[0] ?? '';
	assert.ok(panelRule, 'missing closed compact indicator panel motion rule');
	assert.match(panelRule, /transition:[^;]*opacity\s+2(?:4|5|6|7|8)0ms[^;]*transform\s+2(?:4|5|6|7|8)0ms[^;]*visibility\s+0s\s+linear\s+2(?:4|5|6|7|8)0ms/s);
	const reduced = dashboardCss.slice(dashboardCss.indexOf('@media (prefers-reduced-motion: reduce)'));
	assert.match(reduced, /\.ops-indicator-panel[\s\S]*transition:\s*none/);
});
