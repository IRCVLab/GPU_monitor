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
