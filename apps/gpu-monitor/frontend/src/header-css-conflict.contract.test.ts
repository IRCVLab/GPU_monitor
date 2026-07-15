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

function declarationBlockExact(css, selector, property) {
	const block = cssBlocks(css).find((candidate) => candidate.selector === selector);
	assert.ok(block, `missing rule for ${selector}`);
	assert.match(block.declarations, new RegExp(`${property}\\s*:`), `missing ${property} declaration for ${selector}`);
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

	const shell = declarationBlock(dashboardCss, '.ops-header-shell', 'grid-template-rows');
	assert.match(shell, /grid-template-rows\s*:\s*minmax\(0,\s*1fr\)(?:;|\s)/, 'expanded shell keeps the header in a collapsible grid track');

	const compactShell = declarationBlock(dashboardCss, '.ops-header-shell.ops-header-compact', 'grid-template-rows');
	assert.match(compactShell, /grid-template-rows\s*:\s*minmax\(0,\s*0fr\)(?:;|\s)/, 'compact shell collapses the reserved header row instead of preserving content height');
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

test('monitor-dashboard.css owns compact header rhythm and viewport-safe left indicator placement', () => {
	const headerInner = declarationBlock(dashboardCss, '.ops-header-inner', 'min-height');
	assert.match(headerInner, /min-height\s*:\s*3\.5rem\b/, 'expanded header rhythm stays in the 56px class');

	const compactInner = declarationBlock(
		dashboardCss,
		'.ops-header-shell.ops-header-compact .ops-header-inner',
		'min-height'
	);
	assert.match(compactInner, /min-height\s*:\s*0\b/, 'compact header removes the inner minimum height from layout flow');
	assert.match(compactInner, /height\s*:\s*0\b/, 'compact header drops the inner box height instead of hiding it inside a tall shell');
	assert.match(compactInner, /padding-block\s*:\s*0\b/, 'compact header removes reserved padding while collapsed');

	const anchor = declarationBlock(dashboardCss, '.ops-indicator-anchor', 'left');
	assert.match(anchor, /position\s*:\s*fixed\b/, 'indicator anchor is fixed to the viewport');
	assert.match(anchor, /left\s*:\s*clamp\(0\.75rem,\s*2vw,\s*1rem\)/, 'indicator anchor follows the content gutter with a safe clamp');
	assert.match(anchor, /right\s*:\s*auto\b/, 'indicator anchor does not pin to the right edge');
	assert.match(anchor, /max-width\s*:\s*calc\(100vw - 1\.5rem\)/, 'indicator anchor never exceeds the viewport width');
	assert.match(anchor, /top\s*:\s*max\(/, 'indicator top offset uses a safe inset calculation');
	assert.match(anchor, /safe-area-inset-top/, 'indicator top offset respects the viewport safe area');
	assert.doesNotMatch(anchor, /inset-inline\s*:/, 'indicator anchor no longer stretches across the viewport');
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

	const indicator = declarationBlockExact(dashboardCss, '.ops-indicator', 'display');
	assert.match(indicator, /width\s*:\s*max-content\b/, 'indicator wraps only its trigger and panel');
	assert.doesNotMatch(indicator, /transform\s*:/, 'indicator no longer uses transform offsets that can push it outside the viewport');
	assert.doesNotMatch(dashboardCss, /translateX\(/, 'indicator geometry never relies on translateX offsets');
});

test('compact indicator trigger has an invisible 24px hit target around the dot', () => {
	const trigger = declarationBlockExact(dashboardCss, '.ops-indicator-trigger', 'min-width');
	assert.match(trigger, /min-width\s*:\s*1\.5rem\b/, 'trigger hit target is at least 24px wide');
	assert.match(trigger, /min-height\s*:\s*1\.5rem\b/, 'trigger hit target is at least 24px tall');
	assert.match(trigger, /max-width\s*:\s*100%(?:;|\s)/, 'trigger cannot overflow its viewport-clamped anchor');
	assert.match(trigger, /border\s*:\s*0\b/, 'trigger has no circular border');
	assert.match(trigger, /background\s*:\s*transparent\b/, 'trigger has no circular background');
	assert.doesNotMatch(trigger, /border-radius\s*:\s*999px/, 'trigger must not draw a circular shell');
});

test('left indicator panel opens toward the page interior', () => {
	const panel = declarationBlockExact(dashboardCss, '.ops-indicator-panel', 'left');
	assert.match(panel, /left\s*:\s*calc\(100% \+ 0\.5rem\)(?:;|\s)/);
	assert.match(panel, /right\s*:\s*auto\b/);
	assert.match(panel, /top\s*:\s*0(?:;|\s)/, 'desktop panel stays inside the safe top inset instead of centering above the trigger');
	assert.match(panel, /transform\s*:\s*translate3d\(-0\.25rem, -0\.08rem, 0\) scale\(0\.96\)(?:;|\s)/, 'desktop panel uses only a subtle inward reveal transform, not viewport-edge centering');
	assert.match(panel, /max-width\s*:\s*calc\(100vw - 1\.5rem\)/, 'panel width is clamped to the viewport');
});

test('collapsed indicator anchor remains mounted and viewport-safe across desktop tablet and mobile lanes', () => {
	const tablet = mediaBlock(dashboardCss, '(max-width: 920px)');
	assert.doesNotMatch(tablet, /\.ops-indicator-anchor[\s\S]*display\s*:\s*none/, 'tablet rules must not hide the anchor');
	assert.doesNotMatch(tablet, /\.ops-indicator\s*\{[\s\S]*transform\s*:/, 'tablet rules must not push the indicator with transforms');

	const mobile = mediaBlock(dashboardCss, '(max-width: 640px)');
	assert.doesNotMatch(mobile, /\.ops-indicator-anchor[\s\S]*display\s*:\s*none/, 'mobile rules must not hide the anchor');
	assert.doesNotMatch(mobile, /\.ops-indicator\s*\{[\s\S]*transform\s*:/, 'mobile rules must not push the indicator with transforms');
	const mobilePanel = declarationBlock(mobile, '.ops-indicator-panel', 'left');
	assert.match(mobilePanel, /left\s*:\s*0\b/, 'mobile panel stays aligned to the anchor');
	assert.match(mobilePanel, /right\s*:\s*auto\b/, 'mobile panel still opens inward from the left edge');
	assert.match(mobilePanel, /top\s*:\s*calc\(100% \+ 0\.5rem\)(?:;|\s)/, 'mobile panel drops below the trigger instead of covering content to the right');
	assert.match(mobilePanel, /transform\s*:\s*translate3d\(0, -0\.2rem, 0\) scale\(0\.97\)(?:;|\s)/, 'mobile panel keeps a real closed reveal transform without desktop centering');
	assert.match(mobilePanel, /max-width\s*:\s*calc\(100vw - 1\.5rem\)/, 'mobile panel remains viewport-clamped');
});

test('compact indicator panel and its network buttons are hit-testable only through the open state class', () => {
	const panel = declarationBlock(dashboardCss, '.ops-indicator-panel', 'pointer-events');
	assert.match(panel, /pointer-events\s*:\s*auto\b/, 'open panel must receive pointer events');
	assert.match(dashboardCss, /\.ops-indicator-panel\.ops-indicator-panel-open\s*\{[\s\S]*display\s*:\s*flex\b/, 'open state class displays the panel');
	assert.doesNotMatch(dashboardCss, /\.ops-indicator:(?:hover|focus-within)\s+\.ops-indicator-panel/, 'CSS hover/focus must not own panel visibility');
	assert.match(pageSource, /class:ops-indicator-panel-open=\{indicatorPanelOpen\}/, 'page binds the open class');
	assert.match(pageSource, /onclick=\{\(\) => selectNetwork\(tab\.value\)\}/, 'network buttons retain click handlers');
});

test('compact header collapses to zero layout height while the indicator remains fixed', () => {
	const compactShell = declarationBlock(
		dashboardCss,
		'.ops-header-shell.ops-header-compact',
		'padding-top'
	);
	assert.match(compactShell, /padding-top\s*:\s*0(?:;|\s)/, 'compact shell must not reserve an indicator lane');
	assert.doesNotMatch(dashboardCss, /--ops-indicator-lane-height|\.dashboard-page\.ops-page-compact/);
	assert.doesNotMatch(pageSource, /indicatorLaneHeightPx|syncIndicatorLaneAfterDom|scheduleIndicatorLaneSync/);
	assert.match(dashboardCss, /\.ops-indicator-anchor\s*\{[\s\S]*position\s*:\s*fixed\b/);
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
	assert.match(dot, /animation\s*:\s*ops-indicator-breathe\s+6s\s+ease-in-out\s+infinite\b/);
	assert.match(dashboardCss, /@keyframes\s+ops-indicator-breathe\b/, 'component stylesheet defines breathing keyframes');
	assert.match(
		dashboardCss,
		/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*?\.ops-refresh-ring__dot[\s\S]*?animation\s*:\s*none[\s\S]*?\}/,
		'reduced motion disables indicator breathing in the component stylesheet'
	);
});


test('compact indicator lane masks passing card content without restoring a full header surface', () => {
	const compactShell = declarationBlock(
		dashboardCss,
		'.ops-header-shell.ops-header-compact',
		'background'
	);
	assert.ok(compactShell.includes('background: linear-gradient('));
	assert.ok(compactShell.includes('var(--ops-bg)'));
	assert.ok(
		compactShell.includes('backdrop-filter: blur(var(--material-blur)) saturate(var(--material-saturation))')
	);
	assert.ok(!compactShell.includes('background: transparent'));
});


test('header controls and dashboard cards share the same framed content gutter', () => {
	assert.ok(pageSource.includes('class="ops-header border-b border-surface-border"'));
	assert.ok(pageSource.includes('ops-header-inner $' + '{pageShellClass} px-4 sm:px-6'));
	assert.ok(
		pageSource.includes("const pageMainClass = 'max-w-7xl mx-auto px-4 py-4 sm:px-6';")
	);
});
