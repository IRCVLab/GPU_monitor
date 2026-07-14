// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const appCss = readFileSync(new URL('./app.css', import.meta.url), 'utf8');
const dashboardCss = readFileSync(new URL('./lib/styles/monitor-dashboard.css', import.meta.url), 'utf8');

function cssBlocks(css) {
	return Array.from(css.matchAll(/([^{}]+)\{([^{}]*)\}/g)).map(([, selector, declarations]) => ({
		selector: selector.trim(),
		declarations
	}));
}

function blocksContaining(css, selectorPart) {
	return cssBlocks(css).filter((block) => block.selector.includes(selectorPart));
}

function declarationBlock(css, selectorPart, property) {
	const block = blocksContaining(css, selectorPart).find((candidate) =>
		new RegExp(`${property}\\s*:`).test(candidate.declarations)
	);
	assert.ok(block, `missing ${property} declaration for ${selectorPart}`);
	return block.declarations;
}

test('app.css does not retain legacy compact header fixed-height overrides', () => {
	const fixedHeightOverrides = blocksContaining(appCss, '.ops-header-compact').filter((block) =>
		/\bheight\s*:\s*(?:48|54|64)px\b/.test(block.declarations)
	);

	assert.deepEqual(
		fixedHeightOverrides.map((block) => block.selector),
		[],
		'app.css must not own fixed compact header/header-inner/header heights'
	);
});

test('app.css does not retain legacy relative 64px compact indicator anchor', () => {
	const compactIndicatorAnchor = blocksContaining(
		appCss,
		'.ops-header-compact .ops-indicator-anchor'
	);

	assert.equal(
		compactIndicatorAnchor.some((block) => /position\s*:\s*relative\b/.test(block.declarations)),
		false,
		'app.css must not make compact indicator anchor relative'
	);
	assert.equal(
		compactIndicatorAnchor.some((block) => /\bheight\s*:\s*64px\b/.test(block.declarations)),
		false,
		'app.css must not reserve a 64px indicator anchor row'
	);
});

test('app.css does not hide the compact header implementation', () => {
	const compactHeaderBlocks = blocksContaining(appCss, '.ops-header-compact .ops-header');

	assert.equal(
		compactHeaderBlocks.some((block) => /display\s*:\s*none\b/.test(block.declarations)),
		false,
		'app.css must not override the component header with display:none'
	);
});

test('monitor-dashboard.css owns compact header rhythm and absolute indicator placement', () => {
	const headerInner = declarationBlock(dashboardCss, '.ops-header-inner', 'min-height');
	assert.match(headerInner, /min-height\s*:\s*4rem\b/, 'expanded header rhythm stays in the 64px class');

	const anchor = declarationBlock(dashboardCss, '.ops-indicator-anchor', 'position');
	assert.match(anchor, /position\s*:\s*absolute\b/, 'indicator anchor is absolute');
	assert.match(anchor, /inset-inline\s*:\s*0\b/, 'indicator anchor spans the shell without adding inline layout');
	assert.match(anchor, /top\s*:\s*clamp\([^;]*12px[^;]*16px[^;]*\)/, 'indicator top offset stays within the 12-16px contract');
	assert.match(anchor, /pointer-events\s*:\s*none\b/, 'indicator anchor does not create layout interaction surface');

	const visibleAnchor = declarationBlock(
		dashboardCss,
		'.ops-header-shell.ops-header-indicator-visible .ops-indicator-anchor',
		'display'
	);
	assert.match(visibleAnchor, /display\s*:\s*block\b/, 'component controls indicator visibility');

	const indicator = declarationBlock(dashboardCss, '.ops-indicator', 'transform');
	assert.match(indicator, /margin-left\s*:\s*auto\b/, 'indicator is pushed to the right gutter');
	assert.match(indicator, /transform\s*:\s*translateX\(calc\(2\.5rem \+ 0\.5rem\)\)/, 'indicator sits outside the content edge');
});

test('monitor-dashboard.css owns slow indicator breathing and reduced motion', () => {
	const dot = declarationBlock(dashboardCss, '.ops-indicator-dot', 'animation');
	assert.match(dot, /animation\s*:\s*ops-indicator-breathe\s+4\.2s\s+ease-in-out\s+infinite\b/);
	assert.match(dashboardCss, /@keyframes\s+ops-indicator-breathe\b/, 'component stylesheet defines breathing keyframes');
	assert.match(
		dashboardCss,
		/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\.ops-indicator-dot[\s\S]*?animation\s*:\s*none[\s\S]*?\}/,
		'reduced motion disables indicator breathing in the component stylesheet'
	);
});
