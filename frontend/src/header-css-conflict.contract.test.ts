// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const appCss = readFileSync(new URL('./app.css', import.meta.url), 'utf8');
const dashboardCss = readFileSync(new URL('./lib/styles/monitor-dashboard.css', import.meta.url), 'utf8');
const pageSource = readFileSync(new URL('./routes/+page.svelte', import.meta.url), 'utf8');

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


function mediaBlock(css, query) {
	const start = css.indexOf(`@media ${query}`);
	assert.notEqual(start, -1, `missing @media ${query}`);
	const open = css.indexOf('{', start);
	let depth = 0;
	for (let index = open; index < css.length; index += 1) {
		if (css[index] === '{') depth += 1;
		if (css[index] === '}') depth -= 1;
		if (depth === 0) return css.slice(open + 1, index);
	}
	assert.fail(`unterminated @media ${query}`);
}

test('app.css does not define component-owned header selectors', () => {
	const componentHeaderSelectors = [
		'.ops-header-shell',
		'.ops-header',
		'.ops-header-inner',
		'.ops-header-compact'
	];
	const activeHeaderDefinitions = cssBlocks(appCss).filter((block) =>
		componentHeaderSelectors.some((selector) =>
			block.selector
				.split(',')
				.map((part) => part.trim())
				.some((part) => part.startsWith(selector))
		)
	);

	assert.deepEqual(
		activeHeaderDefinitions.map((block) => block.selector),
		[],
		'app.css must not define component-owned header shell/header/inner/compact selectors'
	);
});

test('app.css does not retain legacy compact header fixed-height overrides', () => {
	const fixedHeightOverrides = blocksContaining(appCss, '.ops-header-compact').filter((block) =>
		/\bheight\s*:\s*(?:48|52|54|64)px\b/.test(block.declarations)
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

test('monitor-dashboard.css keeps the one-row desktop header inside a 56px border-box rhythm', () => {
	const desktop = mediaBlock(dashboardCss, '(min-width: 921px)');
	const headerInner = declarationBlock(desktop, '.ops-header-inner', 'box-sizing');
	assert.match(headerInner, /box-sizing\s*:\s*border-box(?:;|\s)/, 'desktop header includes padding inside its 56px rhythm');
	assert.match(headerInner, /min-height\s*:\s*3\.5rem(?:;|\s)/, 'desktop expanded header minimum is 56px');
	assert.match(headerInner, /height\s*:\s*3\.5rem(?:;|\s)/, 'desktop expanded header measures as one compact row');
	assert.match(headerInner, /padding-block\s*:\s*0\.[0-5][0-9]*rem(?:;|\s)/, 'desktop header uses restrained in-box vertical padding');

	const compactShell = declarationBlock(dashboardCss, '.ops-header-shell.ops-header-compact', 'grid-template-rows');
	assert.match(compactShell, /grid-template-rows\s*:\s*0fr(?:;|\s)/, 'compact shell still reserves zero row height');
});

test('tablet and mobile header keeps controls to two rows until the narrow fallback', () => {
	const tablet = mediaBlock(dashboardCss, '(max-width: 920px)');
	const headerInner = declarationBlock(tablet, '.ops-header-inner', 'grid-template-columns');
	assert.match(
		headerInner,
		/grid-template-columns\s*:\s*minmax\(0,\s*1fr\)\s+auto(?:;|\s)/,
		'tablet header keeps identity and actions on the first row'
	);

	const actions = declarationBlock(tablet, '.ops-actions', 'justify-content');
	assert.match(actions, /justify-content\s*:\s*flex-end(?:;|\s)/, 'actions stay aligned to the right');
	assert.match(actions, /flex-wrap\s*:\s*nowrap(?:;|\s)/, 'actions do not create a third row');

	const mobileNetwork = declarationBlock(tablet, '.ops-network-mobile', 'grid-column');
	assert.match(mobileNetwork, /grid-column\s*:\s*1\s*\/\s*-1(?:;|\s)/, 'network filter owns the second row');

	const narrow = mediaBlock(dashboardCss, '(max-width: 340px)');
	const narrowHeader = declarationBlock(narrow, '.ops-header-inner', 'grid-template-columns');
	assert.match(narrowHeader, /grid-template-columns\s*:\s*1fr(?:;|\s)/, 'very narrow screens may fall back to one column');
	const narrowNetwork = declarationBlock(narrow, '.ops-network-mobile', 'grid-column');
	assert.match(narrowNetwork, /grid-column\s*:\s*auto(?:;|\s)/, 'narrow fallback releases the spanning network row');
});

test('monitor-dashboard.css owns compact header rhythm and fixed left indicator placement', () => {
	const headerInner = declarationBlock(dashboardCss, '.ops-header-inner', 'min-height');
	assert.match(headerInner, /min-height\s*:\s*3\.5rem\b/, 'expanded header rhythm stays in the 56px class');

	const anchor = declarationBlock(dashboardCss, '.ops-indicator-anchor', 'position');
	assert.match(anchor, /position\s*:\s*fixed\b/, 'indicator anchor is fixed to the viewport');
	assert.match(anchor, /inset-inline\s*:\s*0\b/, 'indicator anchor spans the shell without adding inline layout');
	assert.match(anchor, /top\s*:\s*clamp\([^;]*12px[^;]*16px[^;]*\)/, 'indicator top offset stays within the 12-16px contract');
	assert.match(anchor, /pointer-events\s*:\s*none\b/, 'indicator anchor does not create layout interaction surface');
	assert.match(anchor, /display\s*:\s*block\b/, 'indicator remains mounted so cadence motion stays synchronized');
	assert.match(anchor, /opacity\s*:\s*0\b/, 'hidden indicator is visually transparent');
	assert.match(anchor, /visibility\s*:\s*hidden\b/, 'hidden indicator is not exposed to hit testing');

	const visibleAnchor = declarationBlock(
		dashboardCss,
		'.ops-header-shell.ops-header-indicator-visible .ops-indicator-anchor',
		'opacity'
	);
	assert.match(visibleAnchor, /opacity\s*:\s*1\b/, 'component controls indicator visibility');
	assert.match(visibleAnchor, /visibility\s*:\s*visible\b/, 'visible indicator is exposed to interaction');

	const indicator = declarationBlock(dashboardCss, '.ops-indicator', 'transform');
	assert.match(indicator, /margin-right\s*:\s*auto\b/, 'indicator is held at the left edge');
	assert.match(indicator, /margin-left\s*:\s*0\b/, 'indicator does not drift to the right');

	const desktopGutter = mediaBlock(dashboardCss, '(min-width: 1200px)');
	const gutterIndicator = declarationBlock(desktopGutter, '.ops-indicator', 'transform');
	assert.match(gutterIndicator, /transform\s*:\s*translateX\(calc\(-0\.55rem - 0\.5rem\)\)/, '1200px+ gutter moves the bare ring outside the content edge');
});

test('compact indicator trigger has an invisible 24px hit target around the dot', () => {
	const trigger = declarationBlock(dashboardCss, '.ops-indicator-trigger', 'min-width');
	assert.match(trigger, /min-width\s*:\s*1\.5rem\b/, 'trigger hit target is at least 24px wide');
	assert.match(trigger, /min-height\s*:\s*1\.5rem\b/, 'trigger hit target is at least 24px tall');
	assert.match(trigger, /border\s*:\s*0\b/, 'trigger has no circular border');
	assert.match(trigger, /background\s*:\s*transparent\b/, 'trigger has no circular background');
	assert.doesNotMatch(trigger, /border-radius\s*:\s*999px/, 'trigger must not draw a circular shell');
});

test('left indicator panel opens toward the page interior', () => {
	const panel = declarationBlock(dashboardCss, '.ops-indicator-panel', 'left');
	assert.match(panel, /left\s*:\s*0\b/);
	assert.match(panel, /right\s*:\s*auto\b/);
});

test('narrow indicator stays in the outer gutter while its panel stays on screen', () => {
	const tablet = mediaBlock(dashboardCss, '(max-width: 1199px)');
	const tabletIndicator = declarationBlock(tablet, '.ops-indicator', 'transform');
	const tabletPanel = declarationBlock(tablet, '.ops-indicator-panel', 'left');
	assert.match(tabletIndicator, /transform\s*:\s*translateX\(0\)/, 'tablet visible ring begins inside the viewport');
	assert.match(tabletPanel, /left\s*:\s*0\b/, 'tablet panel begins at the viewport edge');

	const mobile = mediaBlock(dashboardCss, '(max-width: 640px)');
	const mobileIndicator = declarationBlock(mobile, '.ops-indicator', 'transform');
	const mobilePanel = declarationBlock(mobile, '.ops-indicator-panel', 'left');
	const mobileRing = declarationBlock(mobile, ".ops-refresh-ring[data-variant='floating']", 'width');
	assert.match(mobileIndicator, /transform\s*:\s*translateX\(0\)/, 'mobile ring keeps a visible inset from the viewport edge');
	assert.match(mobilePanel, /left\s*:\s*2px\b/, 'mobile panel begins at the viewport edge');
	assert.match(mobileRing, /width\s*:\s*0\.75rem\b/, 'mobile painted ring fits wholly inside the 16px page gutter');
	assert.match(mobileRing, /height\s*:\s*0\.75rem\b/, 'mobile ring remains circular');
});

test('compact indicator panel and its network buttons are hit-testable only through the open state class', () => {
	const panel = declarationBlock(dashboardCss, '.ops-indicator-panel', 'pointer-events');
	assert.match(panel, /pointer-events\s*:\s*auto\b/, 'open panel must receive pointer events');
	assert.match(dashboardCss, /\.ops-indicator-panel\.ops-indicator-panel-open\s*\{[\s\S]*display\s*:\s*flex\b/, 'open state class displays the panel');
	assert.doesNotMatch(dashboardCss, /\.ops-indicator:(?:hover|focus-within)\s+\.ops-indicator-panel/, 'CSS hover/focus must not own panel visibility');
	assert.match(pageSource, /class:ops-indicator-panel-open=\{indicatorPanelOpen\}/, 'page binds the open class');
	assert.match(pageSource, /onclick=\{\(\) => selectNetwork\(tab\.value\)\}/, 'network buttons retain click handlers');
});

test('monitor-dashboard.css owns header menu positioning and stacking', () => {
	const menu = declarationBlock(dashboardCss, '.ops-view-menu', 'position');
	assert.match(menu, /position\s*:\s*absolute(?:;|\s)/, 'header menus are absolutely positioned by the component stylesheet');
	assert.match(menu, /top\s*:\s*calc\(100% \+ 0\.6rem\)/, 'header menus are anchored below their controls');
	assert.match(menu, /right\s*:\s*0(?:;|\s)/, 'header menus align to their control edge');
	assert.match(menu, /z-index\s*:\s*60(?:;|\s)/, 'header menus retain component-owned stacking above dashboard content');
});

test('open header menus escape the collapse clip without disabling the compact-state clip', () => {
	assert.match(
		pageSource,
		/class:ops-header-menu-open=\{viewMenuOpen\s*\|\|\s*actionsMenuOpen\}/,
		'page marks the header shell while either popover is open'
	);

	const baseHeader = declarationBlock(dashboardCss, '.ops-header', 'overflow');
	assert.match(baseHeader, /overflow\s*:\s*hidden(?:;|\s)/, 'base header keeps the grid-collapse clip');

	const openHeader = declarationBlock(
		dashboardCss,
		'.ops-header-shell.ops-header-menu-open .ops-header',
		'overflow'
	);
	assert.match(openHeader, /overflow\s*:\s*visible(?:;|\s)/, 'an open menu is not clipped to the 65px header');
});

test('monitor-dashboard.css owns slow indicator breathing and reduced motion', () => {
	const dot = declarationBlock(dashboardCss, '.ops-refresh-ring__dot', 'animation');
	assert.match(dot, /animation\s*:\s*ops-indicator-breathe\s+6(?:\.[0-9]+)?s\s+ease-in-out\s+infinite\b/);
	assert.match(dashboardCss, /@keyframes\s+ops-indicator-breathe\b/, 'component stylesheet defines breathing keyframes');
	assert.match(
		dashboardCss,
		/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\.ops-refresh-ring__dot[\s\S]*?animation\s*:\s*none[\s\S]*?\}/,
		'reduced motion disables indicator breathing in the component stylesheet'
	);
});
