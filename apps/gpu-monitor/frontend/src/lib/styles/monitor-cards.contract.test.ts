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

function cssRuleWithDeclaration(selector, property) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`, 'gm'))];
	const match = matches.find((candidate) => new RegExp(`${escapedProperty}\\s*:`).test(candidate.groups.body));
	assert.ok(match?.groups?.body, `Missing ${property} declaration for ${selector}`);
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

test('task 3 dense full footer spacing keeps the approved compact tokens', () => {
	const footerRule = cssRule('.monitor-card__footer');
	assert.ok(remValues(declarationValue(footerRule, 'gap'))[0] <= 0.16);
	assert.ok(remValues(declarationValue(footerRule, 'padding'))[0] <= 0.36);

	const secondSectionRule = cssRule('.monitor-card__footer-section + .monitor-card__footer-section');
	assert.ok(remValues(declarationValue(secondSectionRule, 'padding-top'))[0] <= 0.18);

	const toggleRule = cssRule('.monitor-card__footer-toggle');
	assert.ok(remValues(declarationValue(toggleRule, 'min-height'))[0] <= 1.45);

	const panelStackRule = cssRule(
		'.monitor-card__footer-panel > .monitor-card__metric-stack,\n.monitor-card__footer-panel > .monitor-card__subsection,\n.monitor-card__footer-panel > .monitor-note-list,\n.monitor-card__footer-panel > .note-form'
	);
	assertDeclaration(panelStackRule, 'padding-top', '0.45rem');

	const metricStackRule = cssRule('.monitor-card__metric-stack');
	assertDeclaration(metricStackRule, 'gap', '0.32rem');
});

test('header baseline stays on one dense line without legacy meta or network pills', () => {
	const header = cssRule('.monitor-card__header');
	assert.ok(remValues(declarationValue(header, 'padding'))[0] <= 0.62);

	const titleRow = cssRule('.monitor-card__title-row');
	assert.match(titleRow, /display:\s*flex/);
	assert.match(titleRow, /align-items:\s*center/);

	const titleLine = cssRule('.monitor-card__title-line');
	assert.match(titleLine, /display:\s*flex/);
	assert.match(titleLine, /flex-wrap:\s*nowrap/);
	assert.match(titleLine, /min-width:\s*0/);

	const hostRule = cssRule('.monitor-card__host');
	assert.match(hostRule, /overflow:\s*hidden/);
	assert.match(hostRule, /text-overflow:\s*ellipsis/);

	assert.doesNotMatch(css, /\.monitor-card__meta\b/, 'legacy split meta row styles should be removed');
	assert.doesNotMatch(css, /\.monitor-card__network\b/, 'legacy network pill styles should be removed');
});

test('mobile full cards keep server identity and edit control on one dense row', () => {
	const card = cssRule('.monitor-card');
	assertDeclaration(card, 'min-width', '0');

	const mobile = mediaBlock('(max-width: 640px)');
	assert.match(mobile, /\.monitor-card__title-row\s*\{[^}]*flex-direction:\s*row;/s);
	assert.match(mobile, /\.monitor-card__edit-button\s*\{[^}]*align-self:\s*flex-start;/s);
	assert.doesNotMatch(mobile, /\.monitor-card__title-row\s*\{[^}]*flex-direction:\s*column;/s);
});

test('System and Memo disclosure motion uses mounted grid-track animation without display or height shortcuts', () => {
	const shellRule = cssRule('.monitor-card__disclosure-shell');
	assertDeclaration(shellRule, 'display', 'grid');
	assertDeclaration(shellRule, 'grid-template-rows', '0fr');
	assertDeclaration(shellRule, 'overflow', 'hidden');
	assertDeclaration(shellRule, 'opacity', '0');
	assert.match(shellRule, /transform:\s*translateY\(-0\.12rem\)/);
	assert.match(shellRule, /transition:[^;]*grid-template-rows[^;]*opacity[^;]*transform[^;]*visibility/s);
	assert.match(shellRule, /visibility\s+0s\s+linear\s+180ms/, 'closing visibility should wait until collapse finishes');
	assertDeclaration(shellRule, 'pointer-events', 'none');

	const expandedRule = cssRule(".monitor-card__disclosure-shell[data-expanded='true']");
	assertDeclaration(expandedRule, 'grid-template-rows', '1fr');
	assertDeclaration(expandedRule, 'opacity', '1');
	assertDeclaration(expandedRule, 'transform', 'translateY(0)');
	assertDeclaration(expandedRule, 'visibility', 'visible');
	assertDeclaration(expandedRule, 'pointer-events', 'auto');
	assert.match(expandedRule, /transition-delay:\s*0s/);

	const innerRule = cssRule('.monitor-card__disclosure-inner');
	assertDeclaration(innerRule, 'min-height', '0');
	assertDeclaration(innerRule, 'overflow', 'hidden');

	assert.doesNotMatch(css, /\.monitor-card__disclosure-shell[^{]*\{[^}]*display:\s*none/s, 'mounted disclosure shell must not toggle display');
	assert.doesNotMatch(css, /max-height\s*:/, 'Task 4a forbids hard-coded max-height disclosure animation');
	assert.doesNotMatch(css, /transition[^;]*(?:height|top)/, 'Task 4a forbids height/top transition shortcuts');
});

test('reduced motion makes disclosure and load gauge state changes effectively immediate', () => {
	const reduced = mediaBlock('(prefers-reduced-motion: reduce)');
	assert.match(reduced, /\.monitor-card__disclosure-shell\s*\{[^}]*transition-duration:\s*1ms;/s);
	assert.match(reduced, /\.monitor-card__footer-disclosure\s*\{[^}]*transition-duration:\s*1ms;/s);
	assert.match(reduced, /\.monitor-card__load-gauge::after,\s*\.monitor-card__load-gauge-fill\s*\{[^}]*transition:\s*none;/s);
});

test('collapsed utility controls keep the chevron affordance and remove decorative markers', () => {
	assert.doesNotMatch(css, /\.monitor-card__footer-marker\b/, 'decorative marker styles should be removed');

	const disclosureRule = cssRule('.monitor-card__footer-disclosure');
	assert.match(disclosureRule, /border-right:\s*1px solid/);
	assert.match(disclosureRule, /border-bottom:\s*1px solid/);
	assertDeclaration(disclosureRule, 'box-sizing', 'border-box');
	assertDeclaration(disclosureRule, 'transform', 'rotate(45deg)');

	const expandedRule = cssRule('.monitor-card__footer-disclosure.is-expanded');
	assertDeclaration(expandedRule, 'transform', 'rotate(225deg)');

	const mobile = mediaBlock('(max-width: 640px)');
	assert.match(mobile, /\.monitor-card__footer-toggle\s*\{[^}]*flex-direction:\s*row;/s);
	assert.match(mobile, /\.monitor-card__footer-toggle\s*\{[^}]*align-items:\s*center;/s);

	const previewRule = cssRule('.monitor-card__footer-preview');
	assertDeclaration(previewRule, 'min-width', '0');
	assertDeclaration(previewRule, 'flex', '1 1 0');
	assertDeclaration(previewRule, 'overflow', 'hidden');
	assertDeclaration(previewRule, 'text-overflow', 'ellipsis');
	assertDeclaration(previewRule, 'white-space', 'nowrap');
});

test('collapsed system preview keeps one load-leading resource summary line with a micro-gauge', () => {
	const previewRule = cssRule('.monitor-card__system-preview');
	assert.match(previewRule, /display:\s*flex/);
	assert.match(previewRule, /align-items:\s*center/);
	assert.match(previewRule, /justify-content:\s*flex-end/);
	assert.match(previewRule, /flex-wrap:\s*nowrap/);
	assert.match(previewRule, /overflow:\s*hidden/);
	assert.ok(remValues(declarationValue(previewRule, 'gap'))[0] <= 0.35);

	const loadPreviewRule = cssRule('.monitor-card__load-preview');
	assert.match(loadPreviewRule, /display:\s*inline-flex/);
	assert.match(loadPreviewRule, /align-items:\s*center/);
	assert.match(loadPreviewRule, /min-width:\s*0/);

	const inlineMetricRule = cssRule('.monitor-card__system-inline-metric');
	assert.match(inlineMetricRule, /display:\s*inline/);
	assert.ok(remValues(declarationValue(inlineMetricRule, 'font-size'))[0] <= 0.66);

	const gaugeRule = cssRule('.monitor-card__load-gauge');
	assert.match(gaugeRule, /width:\s*2\.15rem/);
	assert.match(gaugeRule, /height:\s*0\.26rem/);
	assert.match(gaugeRule, /border-radius:\s*999px/);

	const textRule = cssRule('.monitor-card__load-text');
	assert.ok(remValues(declarationValue(textRule, 'font-size'))[0] <= 0.68);
	assert.match(textRule, /font-variant-numeric:\s*tabular-nums/);

	assert.doesNotMatch(css, /\.monitor-card__system-preview-segment/, 'old segmented preview styles should be removed');
	assert.doesNotMatch(css, /\.monitor-card__system-preview-item/, 'old 4-item system tiles should not remain');
});

test('expanded system removes summary tiles and keeps dense pressure detail', () => {
	assert.doesNotMatch(css, /\.monitor-card__system-summary/, 'old summary tile grid styles should be removed');
	assert.doesNotMatch(css, /\.monitor-card__summary-item/, 'old summary tile styles should be removed');

	const ioDetail = cssRule('.monitor-card__io-detail');
	assert.match(ioDetail, /display:\s*grid|display:\s*flex/);
	assert.match(ioDetail, /gap:\s*0\./);

	const ioMetrics = cssRule('.monitor-card__io-detail-metrics');
	assert.match(ioMetrics, /display:\s*flex|display:\s*grid/);
	assert.match(ioMetrics, /font-variant-numeric:\s*tabular-nums/);

	assert.doesNotMatch(css, /MB\/s/, 'system CSS should not encode MB\/s throughput copy');
});

test('hardware mounts and notes align to the dense card scale', () => {
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

test('expanded system secondary details stay under the dense type scale', () => {
	for (const selector of [
		'.monitor-card__storage-meta',
		'.monitor-card__storage-time',
		'.monitor-card__mount-path',
		'.monitor-card__hardware-index',
		'.monitor-card__hardware-value',
		'.monitor-card__mount-usage',
		'.monitor-card__mount-percent',
		'.monitor-card__io-detail-copy',
		'.monitor-card__io-detail-metrics'
	]) {
		assertFontSizeAtMost(selector, 0.7);
	}
});

test('task 3 memo loading and error expanded states use dense top padding', () => {
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

test('GPU advisory hold cue stays dense while the exact index gets a visible hold collar and notch', () => {
	const cueRule = cssRule('.monitor-gpu-row__hold-cue');
	assert.match(cueRule, /display:\s*inline-flex/);
	assert.match(cueRule, /font-size:\s*0\.6[0-9]rem/);
	assert.match(cueRule, /line-height:\s*1/);
	assert.match(cueRule, /pointer-events:\s*none/);
	assert.match(cueRule, /color:\s*color-mix\(in srgb, #f59e0b/);

	const indexRule = cssRule('.monitor-gpu-row__index');
	assert.match(indexRule, /position:\s*relative/);

	const heldIndexRule = cssRule(".monitor-gpu-row__index[data-has-hold='true']");
	assert.match(heldIndexRule, /box-shadow:\s*inset|outline:/);

	const notchRule = cssRule(".monitor-gpu-row__index[data-has-hold='true']::after");
	assert.match(notchRule, /content:\s*''/);
	assert.match(notchRule, /position:\s*absolute/);
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

test('full gpu metric fills share one accent while memory stays quieter than util', () => {
	const utilRule = cssRule('.monitor-gpu-metric__fill--util');
	assertDeclaration(utilRule, 'background', 'var(--ops-primary)');

	const memoryRule = cssRule('.monitor-gpu-metric__fill--memory');
	assert.match(memoryRule, /background:\s*color-mix\(in srgb, var\(--ops-primary\)/);
	assert.doesNotMatch(memoryRule, /var\(--chart-1\)|var\(--chart-2\)/);
});
