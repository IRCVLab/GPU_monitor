// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import test from 'node:test';
// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import assert from 'node:assert/strict';
// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import { readFileSync } from 'node:fs';
import {
	compensateHeaderScrollPosition,
	HEADER_SCROLL_DIRECTION_THRESHOLD_PX,
	HEADER_TOP_RESET_PX,
	shouldRevealSettledHeaderAtTop,
	updateHeaderVisibility
// @ts-expect-error Node strip-types executes the .ts helper directly.
} from './headerVisibility.ts';

test('compensates in-flow header collapse so layout anchoring does not reverse scroll direction', () => {
	const effectivePositions = [
		{ scrollY: 80, renderedHeight: 65 },
		{ scrollY: 75, renderedHeight: 60 },
		{ scrollY: 63, renderedHeight: 48 },
		{ scrollY: 50, renderedHeight: 35 },
		{ scrollY: 16, renderedHeight: 1 }
	].map(({ scrollY, renderedHeight }) =>
		compensateHeaderScrollPosition(scrollY, 65, renderedHeight)
	);

	assert.deepEqual(effectivePositions, [80, 80, 80, 80, 80]);
});

test('preserves real user scroll distance when the header height is stable', () => {
	assert.equal(compensateHeaderScrollPosition(0, 65, 65), 0);
	assert.equal(compensateHeaderScrollPosition(48, 65, 65), 48);
	assert.equal(compensateHeaderScrollPosition(144, 65, 1), 208);
});

test('reveals at the real page top only after the compact transition has settled', () => {
	assert.equal(shouldRevealSettledHeaderAtTop(0, true, 1), true);
	assert.equal(shouldRevealSettledHeaderAtTop(HEADER_TOP_RESET_PX, true, 1), true);
	assert.equal(shouldRevealSettledHeaderAtTop(0, true, 24), false);
	assert.equal(shouldRevealSettledHeaderAtTop(0, false, 65), false);
});

test('keeps a compact header compact on subthreshold downward motion', () => {
	const result = updateHeaderVisibility({
		currentY: 18,
		previousY: 0,
		direction: null,
		accumulatedDelta: 0,
		currentCompact: true,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, true);
});

test('keeps an expanded header expanded on subthreshold upward motion', () => {
	const result = updateHeaderVisibility({
		currentY: 24,
		previousY: 42,
		direction: 'up',
		accumulatedDelta: 18,
		currentCompact: false,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, false);
});

test('top reset expands and clears accumulated motion', () => {
	const result = updateHeaderVisibility({
		currentY: HEADER_TOP_RESET_PX,
		previousY: 64,
		direction: 'up',
		accumulatedDelta: 80,
		currentCompact: true,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, false);
	assert.equal(result.indicatorVisible, false);
	assert.equal(result.nextAccumulatedDelta, 0);
});

test('compacts only after accumulated downward threshold', () => {
	const below = updateHeaderVisibility({
		currentY: HEADER_SCROLL_DIRECTION_THRESHOLD_PX - 1,
		previousY: 0,
		direction: null,
		accumulatedDelta: 0,
		currentCompact: false,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});
	const crossed = updateHeaderVisibility({
		currentY: HEADER_SCROLL_DIRECTION_THRESHOLD_PX,
		previousY: 0,
		direction: null,
		accumulatedDelta: 0,
		currentCompact: false,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(below.compact, false);
	assert.equal(crossed.compact, true);
});

test('direction change resets accumulation before expanding', () => {
	const result = updateHeaderVisibility({
		currentY: 70,
		previousY: 90,
		direction: 'down',
		accumulatedDelta: 28,
		currentCompact: true,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, true);
	assert.equal(result.nextDirection, 'up');
	assert.equal(result.nextAccumulatedDelta, 20);
});

test('desktop indicator visibility is independent of scroll position', () => {
	const result = updateHeaderVisibility({
		currentY: 24,
		previousY: 54,
		direction: 'up',
		accumulatedDelta: 30,
		currentCompact: true,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, false);
	assert.equal(result.indicatorVisible, false);
});


test('compact indicator is visible at the 921px desktop lane edge without an outer gutter', () => {
	const result = updateHeaderVisibility({
		currentY: 80,
		previousY: 40,
		direction: 'down',
		accumulatedDelta: 0,
		currentCompact: false,
		reducedMotion: false,
		hasOuterGutter: false,
		viewportWidth: 921
	});

	assert.equal(result.compact, true);
	assert.equal(result.indicatorVisible, true);
});

test('compact indicator remains hidden at the 920px mobile cutoff', () => {
	const result = updateHeaderVisibility({
		currentY: 80,
		previousY: 40,
		direction: 'down',
		accumulatedDelta: 0,
		currentCompact: false,
		reducedMotion: false,
		hasOuterGutter: true,
		viewportWidth: 920
	});

	assert.equal(result.compact, true);
	assert.equal(result.indicatorVisible, false);
});

test('reduced motion preserves immediate threshold semantics without timers', () => {
	const result = updateHeaderVisibility({
		currentY: 60,
		previousY: 24,
		direction: 'down',
		accumulatedDelta: 0,
		currentCompact: false,
		reducedMotion: true,
		hasOuterGutter: true,
		viewportWidth: 1440
	});

	assert.equal(result.compact, true);
	assert.equal(result.nextPreviousY, 60);
});


const pageSource = readFileSync(new URL('../../routes/+page.svelte', import.meta.url), 'utf8');
const dashboardCss = readFileSync(new URL('../styles/monitor-dashboard.css', import.meta.url), 'utf8');

function functionBody(source: string, name: string): string {
	const start = source.indexOf(`function ${name}`);
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

test('page derives header direction from a layout-compensated scroll position', () => {
	assert.match(pageSource, /bind:this=\{headerShellElement\}/);
	assert.match(pageSource, /bind:this=\{headerSurfaceElement\}/);

	const positionBody = functionBody(pageSource, 'currentHeaderScrollPosition');
	assert.match(positionBody, /compensateHeaderScrollPosition/);
	assert.match(positionBody, /shouldRevealSettledHeaderAtTop/);
	assert.match(positionBody, /headerSurfaceElement\.scrollHeight/);
	assert.match(positionBody, /headerShellElement\.getBoundingClientRect\(\)\.height/);

	const scrollBody = functionBody(pageSource, 'updateHeaderFromScroll');
	assert.match(scrollBody, /currentY\s*=\s*currentHeaderScrollPosition\(\)/);
});

test('manual header reveal resets accumulated scroll state to the current viewport position', () => {
	const body = functionBody(pageSource, 'revealHeader');

	assert.match(body, /headerCompact\s*=\s*false/);
	assert.match(body, /headerIndicatorVisible\s*=\s*false/);
	assert.match(body, /headerScrollDirection\s*=\s*null/);
	assert.match(body, /headerScrollDistance\s*=\s*0/);
	assert.match(body, /browser[\s\S]*currentHeaderScrollPosition\(\)/);
	assert.match(body, /headerPreviousY\s*=\s*currentHeaderScrollPosition\(\)/);
});

test('header resize recomputes indicator visibility and unregisters the passive listener', () => {
	assert.match(pageSource, /function\s+handleHeaderResize\s*\(/);
	assert.match(pageSource, /window\.addEventListener\(\s*'resize'\s*,\s*handleHeaderResize\s*,\s*\{\s*passive:\s*true\s*\}\s*\)/);
	assert.match(pageSource, /window\.removeEventListener\(\s*'resize'\s*,\s*handleHeaderResize\s*\)/);

	const resizeBody = functionBody(pageSource, 'handleHeaderResize');
	assert.match(resizeBody, /headerHasOuterGutter\(window\.innerWidth\)/);
	assert.match(resizeBody, /viewportWidth:\s*window\.innerWidth/);
	assert.match(resizeBody, /headerIndicatorVisible\s*=\s*result\.indicatorVisible/);
});

test('upward wheel, touch, and keyboard intent can reveal a settled compact header at scroll zero', () => {
	const wheelBody = functionBody(pageSource, 'handleHeaderWheel');
	assert.match(wheelBody, /event\.deltaY\s*<\s*0/);
	assert.match(wheelBody, /shouldRevealHeaderForUpwardIntent\(\)/);
	assert.match(wheelBody, /revealHeader\(\)/);

	const touchBody = functionBody(pageSource, 'handleHeaderTouchMove');
	assert.match(touchBody, /nextY\s*>\s*headerTouchY/);
	assert.match(touchBody, /shouldRevealHeaderForUpwardIntent\(\)/);
	assert.match(touchBody, /revealHeader\(\)/);

	const keyboardBody = functionBody(pageSource, 'handleWindowKeydown');
	assert.match(keyboardBody, /'Home'/);
	assert.match(keyboardBody, /'ArrowUp'/);
	assert.match(keyboardBody, /'PageUp'/);
	assert.match(keyboardBody, /shouldRevealHeaderForUpwardIntent\(\)/);

	for (const eventName of ['wheel', 'touchstart', 'touchmove', 'touchend']) {
		assert.match(pageSource, new RegExp(`window\\.addEventListener\\(\\s*'${eventName}'`));
		assert.match(pageSource, new RegExp(`window\\.removeEventListener\\(\\s*'${eventName}'`));
	}
});

test('header transition completion resynchronizes direction baseline after layout anchoring settles', () => {
	assert.match(pageSource, /ontransitionend=\{handleHeaderTransitionEnd\}/);
	const body = functionBody(pageSource, 'handleHeaderTransitionEnd');
	assert.match(body, /event\.target\s*!==\s*headerShellElement/);
	assert.match(body, /event\.propertyName\s*!==\s*'grid-template-rows'/);
	assert.match(body, /headerPreviousY\s*=\s*currentHeaderScrollPosition\(\)/);
	assert.match(body, /headerScrollDirection\s*=\s*null/);
	assert.match(body, /headerScrollDistance\s*=\s*0/);
});

test('desktop indicator has a defensive CSS cutoff only below the 921px desktop lane', () => {
	assert.doesNotMatch(dashboardCss, /@media\s*\(max-width:\s*1199px\)[\s\S]*\.ops-indicator-anchor\s*\{[\s\S]*display:\s*none\s*!important\s*;/);
	assert.match(dashboardCss, /@media\s*\(max-width:\s*920px\)[\s\S]*\.ops-indicator-anchor\s*\{[\s\S]*display:\s*none\s*;/);
});

test('compact CSS does not reopen the full header on hover or focus', () => {
	assert.doesNotMatch(dashboardCss, /\.ops-header-shell\.ops-header-compact:(?:hover|focus-within)\s*\{/);
	assert.doesNotMatch(dashboardCss, /\.ops-header-shell\.ops-header-compact:(?:hover|focus-within)\s+\.ops-header\s*\{/);
});

test('header surface is hidden from interaction while the indicator remains independently focusable', () => {
	assert.match(pageSource, /<div class=\{`ops-indicator-anchor \$\{pageShellClass\}`\} aria-hidden=\{!headerIndicatorVisible\}/);
	assert.match(pageSource, /<button\s+[\s\S]*class="ops-indicator-trigger"[\s\S]*>/);
	assert.match(pageSource, /<header\s+[\s\S]*inert=\{headerCompact\}[\s\S]*aria-hidden=\{headerCompact\}/);
});

test('desktop indicator is shrink-wrapped with separate edge and gutter lanes', () => {
	const indicatorRule = dashboardCss.match(/\.ops-indicator\s*\{(?<body>[^}]*)\}/m)?.groups?.body ?? '';

	assert.match(indicatorRule, /width:\s*max-content\s*;/);
	assert.match(indicatorRule, /margin-left:\s*auto\s*;/);
	assert.doesNotMatch(indicatorRule, /translateX\(\s*calc\(\s*100%/);
	assert.match(indicatorRule, /transform:\s*translateX\(\s*calc\(\s*-0\.55rem\s*-\s*0\.5rem\s*\)\s*\)\s*;/);
	assert.match(dashboardCss, /@media\s*\(min-width:\s*1200px\)[\s\S]*\.ops-indicator\s*\{[\s\S]*transform:\s*translateX\(\s*calc\(\s*0\.55rem\s*\+\s*0\.5rem\s*\)\s*\)\s*;/);
});
