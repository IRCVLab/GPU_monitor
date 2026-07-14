// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import test from 'node:test';
// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import assert from 'node:assert/strict';
// @ts-expect-error node built-in types are not installed for these stripped Node tests.
import { readFileSync } from 'node:fs';
import {
	HEADER_SCROLL_DIRECTION_THRESHOLD_PX,
	HEADER_TOP_RESET_PX,
	updateHeaderVisibility
// @ts-expect-error Node strip-types executes the .ts helper directly.
} from './headerVisibility.ts';

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

test('manual header reveal resets accumulated scroll state to the current viewport position', () => {
	const body = functionBody(pageSource, 'revealHeader');

	assert.match(body, /headerCompact\s*=\s*false/);
	assert.match(body, /headerIndicatorVisible\s*=\s*false/);
	assert.match(body, /headerScrollDirection\s*=\s*null/);
	assert.match(body, /headerScrollDistance\s*=\s*0/);
	assert.match(body, /browser[\s\S]*window\.scrollY/);
	assert.match(body, /headerPreviousY\s*=\s*Math\.max\(0,\s*window\.scrollY\)/);
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

test('desktop indicator has a defensive CSS cutoff below 1200px', () => {
	assert.match(dashboardCss, /@media\s*\(max-width:\s*1199px\)[\s\S]*\.ops-indicator-anchor\s*\{[\s\S]*display:\s*none\s*!important\s*;/);
});


test('desktop indicator is shrink-wrapped and uses a fixed gutter shift instead of full-width translation', () => {
	const indicatorRule = dashboardCss.match(/\.ops-indicator\s*\{(?<body>[^}]*)\}/m)?.groups?.body ?? '';

	assert.match(indicatorRule, /width:\s*max-content\s*;/);
	assert.match(indicatorRule, /margin-left:\s*auto\s*;/);
	assert.doesNotMatch(indicatorRule, /translateX\(\s*calc\(\s*100%/);
	assert.match(indicatorRule, /transform:\s*translateX\(\s*calc\(\s*2\.5rem\s*\+\s*0\.5rem\s*\)\s*\)\s*;/);
});
