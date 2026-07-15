// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('./monitor-cards.css', import.meta.url), 'utf8');

function cssRule(selector) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = css.match(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`, 'm'));
	assert.ok(match?.groups?.body, `Missing CSS rule for ${selector}`);
	return match.groups.body;
}

function mediaBlock(query) {
	const start = css.indexOf(`@media ${query}`);
	assert.notEqual(start, -1, `Missing @media ${query}`);
	const open = css.indexOf('{', start);
	let depth = 0;
	for (let index = open; index < css.length; index += 1) {
		if (css[index] === '{') depth += 1;
		if (css[index] === '}') depth -= 1;
		if (depth === 0) return css.slice(open + 1, index);
	}
	assert.fail(`Unterminated @media ${query}`);
}


function cssRuleWithDeclaration(selector, property) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`, 'gm'))];
	const match = matches.find((candidate) => new RegExp(`${escapedProperty}\\s*:`).test(candidate.groups.body));
	assert.ok(match?.groups?.body, `Missing ${property} declaration for ${selector}`);
	return match.groups.body;
}


function assertDeclaration(rule, property, value) {
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const escapedValue = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	assert.match(rule, new RegExp(`${escapedProperty}\\s*:\\s*${escapedValue}\\s*;`));
}

function remValues(value) {
	const values = [...value.matchAll(/([0-9]*\.?[0-9]+)rem/g)].map((match) => Number(match[1]));
	assert.ok(values.length > 0, `Expected rem value, got ${value}`);
	return values;
}

function declarationValue(rule, property) {
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = rule.match(new RegExp(`${escapedProperty}\\s*:\\s*(?<value>[^;]+);`));
	assert.ok(match?.groups?.value, `Missing ${property} declaration`);
	return match.groups.value.trim();
}

test('task 4 dense full footer spacing uses exact compact tokens', () => {
	const footerRule = cssRule('.monitor-card__footer');
	assertDeclaration(footerRule, 'gap', '0.28rem');
	assertDeclaration(footerRule, 'padding', '0.5rem 0.75rem 0.55rem');

	const secondSectionRule = cssRule('.monitor-card__footer-section + .monitor-card__footer-section');
	assert.ok(remValues(declarationValue(secondSectionRule, 'padding-top'))[0] <= 0.3);

	const panelStackRule = cssRule('.monitor-card__footer-panel > .monitor-card__metric-stack,\n.monitor-card__footer-panel > .monitor-card__subsection,\n.monitor-card__footer-panel > .monitor-note-list,\n.monitor-card__footer-panel > .note-form');
	assertDeclaration(panelStackRule, 'padding-top', '0.45rem');

	const metricStackRule = cssRule('.monitor-card__metric-stack');
	assertDeclaration(metricStackRule, 'gap', '0.32rem');
});

test('mobile full cards keep server identity and edit control on one dense row', () => {
	const card = cssRule('.monitor-card');
	assertDeclaration(card, 'min-width', '0');

	const mobile = mediaBlock('(max-width: 640px)');
	assert.match(mobile, /\.monitor-card__title-row\s*\{[^}]*flex-direction:\s*row;/s);
	assert.match(mobile, /\.monitor-card__edit-button\s*\{[^}]*align-self:\s*flex-start;/s);
	assert.doesNotMatch(
		mobile,
		/\.monitor-card__title-row\s*,\s*\.monitor-note-item\s*\{[^}]*flex-direction:\s*column;/s
	);
});

test('mobile collapsed utility controls remain one horizontal line with protected disclosure', () => {
	const mobile = mediaBlock('(max-width: 640px)');
	assert.match(mobile, /\.monitor-card__footer-toggle\s*\{[^}]*flex-direction:\s*row;/s);
	assert.match(mobile, /\.monitor-card__footer-toggle\s*\{[^}]*align-items:\s*center;/s);
	assert.doesNotMatch(mobile, /\.monitor-card__footer-toggle\s*\{[^}]*flex-direction:\s*column;/s);

	const mainRule = cssRule('.monitor-card__footer-toggle-main');
	assertDeclaration(mainRule, 'flex', '0 0 auto');

	const sideRule = cssRule('.monitor-card__footer-side');
	assertDeclaration(sideRule, 'min-width', '0');
	assertDeclaration(sideRule, 'flex', '1 1 auto');

	const previewRule = cssRule('.monitor-card__footer-preview');
	assertDeclaration(previewRule, 'overflow', 'hidden');
	assertDeclaration(previewRule, 'text-overflow', 'ellipsis');
	assertDeclaration(previewRule, 'white-space', 'nowrap');
});

test('mobile expanded system keeps hardware and mount telemetry dense without clipping', () => {
	const mobile = mediaBlock('(max-width: 640px)');
	assert.doesNotMatch(
		mobile,
		/\.monitor-card__mount-item\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto;/s
	);
	assert.doesNotMatch(
		mobile,
		/\.monitor-card__mount-usage\s*\{[^}]*grid-column:\s*1 \/ -1;/s
	);

	const hardwareRule = cssRule('.monitor-card__hardware-item');
	assert.ok(Math.max(...remValues(declarationValue(hardwareRule, 'padding'))) <= 0.3);
	assert.ok(Math.max(...remValues(declarationValue(hardwareRule, 'gap'))) <= 0.2);

	const hardwareTypeRule = cssRule('.monitor-card__hardware-index,\n.monitor-card__hardware-value');
	assert.ok(remValues(declarationValue(hardwareTypeRule, 'font-size'))[0] <= 0.66);
});

test('task 4 hardware mounts and notes align to dense card scale', () => {
	const hardwareRule = cssRule('.monitor-card__hardware-item');
	assert.ok(remValues(declarationValue(hardwareRule, 'padding'))[0] <= 0.3);
	assert.ok(remValues(declarationValue(hardwareRule, 'border-radius'))[0] <= 0.5);

	const mountRule = cssRule('.monitor-card__mount-item');
	assert.ok(remValues(declarationValue(mountRule, 'padding'))[0] <= 0.3);
	assert.ok(remValues(declarationValue(mountRule, 'border-radius'))[0] <= 0.5);

	const noteRule = cssRuleWithDeclaration('.monitor-note-item', 'padding');
	assertDeclaration(noteRule, 'padding', '0.46rem 0.55rem');
	assertDeclaration(noteRule, 'border-radius', '0.55rem');
});



function cssRuleContainingSelectorWithDeclaration(selector, property) {
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const matches = [...css.matchAll(/(?<selectors>[^{}]+)\{(?<body>[^}]*)\}/gm)];
	const match = matches.find((candidate) =>
		candidate.groups.selectors.split(',').map((item) => item.trim()).includes(selector) &&
		new RegExp(`${escapedProperty}\\s*:`).test(candidate.groups.body)
	);
	assert.ok(match?.groups?.body, `Missing ${property} declaration for ${selector}`);
	return match.groups.body;
}

function assertFontSizeAtMost(selector, maxRem) {
	const rule = cssRuleContainingSelectorWithDeclaration(selector, 'font-size');
	assert.ok(
		remValues(declarationValue(rule, 'font-size'))[0] <= maxRem,
		`${selector} font-size must be <= ${maxRem}rem`
	);
}

test('task 4 expanded system secondary details stay under dense type scale', () => {
	for (const selector of [
		'.monitor-card__storage-meta',
		'.monitor-card__storage-time',
		'.monitor-card__mount-path',
		'.monitor-card__hardware-index',
		'.monitor-card__hardware-value',
		'.monitor-card__mount-usage',
		'.monitor-card__mount-percent'
	]) {
		assertFontSizeAtMost(selector, 0.7);
	}
});

test('task 4 memo loading and error expanded states use dense top padding', () => {
	const stateRule = cssRuleWithDeclaration('.monitor-card__loading-state,\n.monitor-card__error-row', 'padding-top');
	assert.ok(remValues(declarationValue(stateRule, 'padding-top'))[0] <= 0.45);
});

test('unified GPU selector remains chip-based, compact, and visually integrated', () => {
	assert.doesNotMatch(css, /\.note-form-kind-row|\.note-form-kind-toggle/);
	const rowRule = cssRule('.monitor-card .note-form-gpu-chip-row');
	assert.match(rowRule, /display:\s*flex/);
	assert.match(rowRule, /flex-wrap:\s*wrap/);
	assert.ok(remValues(declarationValue(rowRule, 'gap'))[0] <= 0.3);

	const chipRule = cssRuleWithDeclaration('.monitor-card .note-form-gpu-chip', 'min-height');
	assert.ok(remValues(declarationValue(chipRule, 'min-height'))[0] <= 1.45);
	assert.ok(remValues(declarationValue(chipRule, 'padding'))[0] <= 0.16);
	assert.match(css, /\.note-form-gpu-chip\[aria-pressed='true'\]/);
	assert.match(css, /\.note-form-hold-warning/);
	assert.match(css, /\.monitor-note-item__kind/);
	assert.match(css, /\.monitor-note-item__gpu-chips/);
	assert.match(css, /\.monitor-note-item__gpu-chip/);
});


test('GPU chip hover has focus-visible and reduced-motion coverage', () => {
	assert.match(css, /\.note-form-gpu-chip:focus-visible[\s\S]*box-shadow/);
	assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.note-form-gpu-chip:hover[\s\S]*transform:\s*none/);
});

test('GPU advisory hold cue is dense visible text with subtle noninteractive styling', () => {
	const rule = cssRule('.monitor-gpu-row__hold-cue');
	assert.match(rule, /display:\s*inline-flex/);
	assert.match(rule, /font-size:\s*0\.6[0-9]rem/);
	assert.match(rule, /line-height:\s*1/);
	assert.match(rule, /pointer-events:\s*none/);
	assert.doesNotMatch(rule, /min-height:\s*(?:1\.[2-9]|[2-9])/);
	assert.match(rule, /color:\s*color-mix\(in srgb, #f59e0b/);
	assert.match(css, /\.monitor-gpu-row__users[\s\S]*align-items:\s*center/);
});

test('Mem receives the wider flexible track without wasting a 10ch value column', () => {
	const metricsRule = cssRule('.monitor-gpu-row__metrics');
	assertDeclaration(metricsRule, 'grid-template-columns', 'minmax(0, 0.72fr) minmax(0, 1.28fr)');

	const memoryValueRule = cssRule('.monitor-gpu-metric__value--memory');
	assertDeclaration(memoryValueRule, 'min-width', '8ch');
	assert.doesNotMatch(memoryValueRule, /10ch/);

	const narrowMobile = mediaBlock('(max-width: 520px)');
	assert.doesNotMatch(narrowMobile, /\.monitor-gpu-row__metrics\s*\{[^}]*grid-template-columns:\s*1fr;/s);
});

test('full gpu index uses the selected theme accent with inverted availability states', () => {
	const availableRule = cssRule(".monitor-gpu-row[data-state='available'] .monitor-gpu-row__index");
	assertDeclaration(availableRule, 'color', 'var(--ops-fg)');
	assert.doesNotMatch(availableRule, /(?:^|[\n\r])\s*color:\s*var\(--ops-primary\)\s*;/);
	assertDeclaration(availableRule, 'border-color', 'var(--ops-primary)');
	const occupiedRule = cssRule(".monitor-gpu-row[data-state='occupied'] .monitor-gpu-row__index");
	assertDeclaration(occupiedRule, 'background', 'var(--ops-primary)');
	assertDeclaration(occupiedRule, 'border-color', 'var(--ops-primary)');
	assertDeclaration(occupiedRule, 'color', 'var(--ops-on-primary)');
	assert.doesNotMatch(occupiedRule, /(?:^|[\n\r])\s*color:\s*var\(--ops-primary-fg\)\s*;/);
	const unknownRule = cssRule(".monitor-gpu-row[data-state='unknown'] .monitor-gpu-row__index");
	assert.doesNotMatch(unknownRule, /#f59e0b|var\(--chart-[12]\)|var\(--ops-primary\)/);
});

test('full gpu metric fills share one accent while memory stays quieter than util', () => {
	const utilRule = cssRule('.monitor-gpu-metric__fill--util');
	assertDeclaration(utilRule, 'background', 'var(--ops-primary)');

	const memoryRule = cssRule('.monitor-gpu-metric__fill--memory');
	assert.match(memoryRule, /background:\s*color-mix\(in srgb, var\(--ops-primary\)/);
	assert.doesNotMatch(memoryRule, /var\(--chart-1\)|var\(--chart-2\)/);
});

test('collapsed utility rows use subtle markers and CSS disclosure angles', () => {
	const markerRule = cssRule('.monitor-card__footer-marker');
	assertDeclaration(markerRule, 'width', '0.34rem');
	assertDeclaration(markerRule, 'height', '0.34rem');

	const disclosureRule = cssRule('.monitor-card__footer-disclosure');
	assert.match(disclosureRule, /border-right:\s*1px solid/);
	assert.match(disclosureRule, /border-bottom:\s*1px solid/);
	assertDeclaration(disclosureRule, 'transform', 'rotate(45deg)');

	const expandedRule = cssRule('.monitor-card__footer-disclosure.is-expanded');
	assertDeclaration(expandedRule, 'transform', 'rotate(225deg)');
});

test('task 5 full card header and system density follow the quiet instrument contract', () => {
	const statusTextRule = cssRule('.monitor-card__status-text');
	assert.doesNotMatch(statusTextRule, /display:\s*none/);

	const srOnlyRule = cssRule('.monitor-card__sr-only');
	assert.match(srOnlyRule, /position:\s*absolute/);
	assert.match(srOnlyRule, /width:\s*1px/);
	assert.match(srOnlyRule, /height:\s*1px/);
	assert.match(srOnlyRule, /clip:\s*rect\(0,\s*0,\s*0,\s*0\)/);

	const headerMetaRule = cssRule('.monitor-card__meta');
	assertDeclaration(headerMetaRule, 'flex-wrap', 'nowrap');
	assertDeclaration(headerMetaRule, 'white-space', 'nowrap');

	const previewRule = cssRule('.monitor-card__system-preview');
	assert.match(previewRule, /display:\s*grid/);
	assert.match(previewRule, /grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*max-content\)\)/);

	const previewItemRule = cssRule('.monitor-card__system-preview-item');
	assert.match(previewItemRule, /display:\s*inline-grid/);
	assert.match(previewItemRule, /grid-template-columns:\s*auto/);
	assertDeclaration(previewItemRule, 'gap', '0.08rem');

	const previewLabelRule = cssRule('.monitor-card__system-preview-item small');
	assert.match(previewLabelRule, /text-transform:\s*uppercase/);
	assert.match(previewLabelRule, /font-size:\s*0\.5[0-9]rem/);

	const previewValueRule = cssRule('.monitor-card__system-preview-item strong');
	assert.match(previewValueRule, /font-size:\s*0\.6[0-9]rem/);
	assert.match(previewValueRule, /font-variant-numeric:\s*tabular-nums/);

	const footerToggleRule = cssRule('.monitor-card__footer-toggle');
	assert.ok(remValues(declarationValue(footerToggleRule, 'min-height'))[0] <= 1.875);

	const systemSummaryRule = cssRule('.monitor-card__system-summary');
	assert.match(systemSummaryRule, /display:\s*grid/);
	assert.match(systemSummaryRule, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);

	const hardwareRule = cssRule('.monitor-card__hardware-item');
	assertDeclaration(hardwareRule, 'min-height', '1.5rem');
	assert.match(hardwareRule, /background:\s*transparent/);
	assert.doesNotMatch(hardwareRule, /border-radius:\s*0\.[45]/);

	const mountRule = cssRule('.monitor-card__mount-item');
	assertDeclaration(mountRule, 'min-height', '1.5rem');
	assert.match(mountRule, /background:\s*transparent/);

	const mountListRule = cssRule('.monitor-card__mount-list');
	assert.doesNotMatch(mountListRule, /background:/);

	const mobile = mediaBlock('(max-width: 640px)');
	assert.match(mobile, /\.monitor-card__hardware-item\s*\{[^}]*min-height:\s*1\.5rem;/s);
	assert.match(mobile, /\.monitor-card__mount-item\s*\{[^}]*min-height:\s*1\.5rem;/s);
	assert.doesNotMatch(mobile, /\.monitor-card__hardware-item\s*\{[^}]*min-height:\s*1\.[0-4]/s);
	assert.doesNotMatch(mobile, /\.monitor-card__mount-item\s*\{[^}]*min-height:\s*1\.[0-4]/s);
});

test('memo history and composer are grouped without nested card surfaces', () => {
	const groupRule = cssRule('.monitor-card__memo-group');
	assertDeclaration(groupRule, 'background', 'transparent');
	assert.match(groupRule, /border-top:\s*1px solid/);

	const headRule = cssRule('.monitor-card__memo-group-head');
	assert.match(headRule, /display:\s*flex/);
	assert.match(headRule, /justify-content:\s*space-between/);

	const emptyRule = cssRule('.monitor-card__memo-empty');
	assert.match(emptyRule, /font-size:\s*0\.6[0-9]rem/);
});
