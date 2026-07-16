// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Script, createContext } from 'node:vm';

const storeSource = readFileSync(new URL('./theme.ts', import.meta.url), 'utf8');
const appHtml = readFileSync(new URL('../../app.html', import.meta.url), 'utf8');
const appCss = readFileSync(new URL('../../app.css', import.meta.url), 'utf8');
const appScriptBody = appHtml.match(/<script>\s*([\s\S]*?)\s*<\/script>/)?.[1];

function runInitialTheme(cookie) {
	assert.ok(appScriptBody, 'app.html inline theme script must exist');
	const classes = new Set(['dark']);
	const context = createContext({
		document: {
			cookie,
			documentElement: {
				classList: {
					remove: (...values) => values.forEach((value) => classes.delete(value)),
					add: (value) => classes.add(value)
				},
				dataset: {}
			}
		}
	});

	new Script(appScriptBody).runInContext(context);
	return { classes, material: context.document.documentElement.dataset.material };
}

test('Task 5 theme store exposes semantic material presets only', () => {
	assert.match(storeSource, /materialThemes = \['liquid', 'claude', 'astro'\] as const/);
	assert.match(storeSource, /materialThemeOptions = \[/);
	assert.match(storeSource, /Clean/);
	assert.match(storeSource, /Claude\+/);
	assert.match(storeSource, /AstroVista/);
	assert.doesNotMatch(storeSource, /colorThemes|colorThemeOptions|setColorTheme/);
});

test('Task 5 store and initial HTML migrate old accent values to liquid with matching logic', () => {
	for (const legacyValue of ['blue', 'violet', 'emerald', 'rose', 'pink']) {
		for (const cookieName of ['materialTheme', 'colorTheme', 'theme']) {
			const result = runInitialTheme(`session=abc; ${cookieName}=${legacyValue}`);
			assert.equal(result.material, 'liquid', `${cookieName}=${legacyValue}`);
		}
	}
	for (const material of ['liquid', 'claude', 'astro']) {
		assert.equal(runInitialTheme(`materialTheme=${material}`).material, material);
	}

	assert.match(storeSource, /const MATERIAL_COOKIE = 'materialTheme'/);
	assert.match(storeSource, /const LEGACY_COLOR_COOKIE = 'colorTheme'/);
	assert.match(
		storeSource,
		/readCookie\(MATERIAL_COOKIE\)[\s\S]*readCookie\(LEGACY_COLOR_COOKIE\)[\s\S]*readCookie\(LEGACY_THEME_COOKIE\)/
	);
	assert.match(storeSource, /return normalizeMaterial\(requestedMaterial\) \?\? 'liquid'/);
	assert.match(appHtml, /const requestedMaterial = read\('materialTheme'\) \|\| read\('colorTheme'\) \|\| legacy/);
	assert.match(appHtml, /const material = materialValues\.includes\(requestedMaterial\)\s*\? requestedMaterial\s*: 'liquid'/);
	assert.doesNotMatch(storeSource, /oldMaterialValues|\? 'liquid' : 'liquid'/);
	assert.doesNotMatch(appHtml, /oldMaterialValues|\? 'liquid' : 'liquid'/);
});

test('Task 5 app.html is SSR-safe and initializes the semantic material data attribute', () => {
	assert.match(appHtml, /<html lang="ko" class="dark" data-material="liquid">/);
	assert.match(appHtml, /document\.documentElement\.dataset\.material = material/);
	assert.doesNotMatch(appHtml, /data-color-theme|dataset\.colorTheme|colorTheme =/);
});


test('Task 5 app.html inline cookie reader finds material cookies after earlier cookies', () => {
	const result = runInitialTheme('session=abc; themeMode=light; unrelated=1; materialTheme=astro');
	assert.equal(result.material, 'astro');
	assert.equal(result.classes.has('light'), true);
	assert.equal(result.classes.has('dark'), false);
});


test('Task 6 material presets keep liquid cookie but show Clean label and structural tokens', () => {
	assert.match(storeSource, /\{ value: 'liquid', label: 'Clean', description: 'Crisp mostly opaque material' \}/);
	assert.doesNotMatch(storeSource, /Liquid Glass/);
	assert.match(storeSource, /\{ value: 'claude', label: 'Claude\+', description: 'Warm soft rounded material' \}/);
	assert.match(storeSource, /\{ value: 'astro', label: 'AstroVista', description: 'Cool technical crisp material' \}/);

	for (const material of ['liquid', 'claude', 'astro']) {
		const rule = appCss.match(new RegExp(`html\\[data-material='${material}'\\] \\{(?<body>[\\s\\S]*?)\\n\\}`));
		assert.ok(rule?.groups?.body, `missing ${material} material rule`);
		for (const token of [
			'--material-surface-mix',
			'--material-blur',
			'--material-saturation',
			'--material-border-alpha',
			'--material-highlight-alpha',
			'--material-radius',
			'--material-control-radius',
			'--material-shadow',
			'--material-card-mix',
			'--material-control-mix',
			'--material-veil-mix',
			'--material-tooltip-mix'
		]) {
			assert.match(rule.groups.body, new RegExp(`${token}:`), `${material} missing ${token}`);
		}
	}

	assert.match(appCss, /html\[data-material='liquid'\][\s\S]*--material-surface-mix: 94%;[\s\S]*--material-blur: 6px;[\s\S]*--material-shadow: 0 0\.45rem 1\.1rem/);
	assert.match(appCss, /html\[data-material='claude'\][\s\S]*--material-surface-mix: 88%;[\s\S]*--material-blur: 14px;[\s\S]*--material-radius: 1\.15rem/);
	assert.match(appCss, /html\[data-material='astro'\][\s\S]*--material-surface-mix: 91%;[\s\S]*--material-blur: 8px;[\s\S]*--material-radius: 0\.55rem/);
});


test('theme mode and material preferences read and write cookies only', () => {
	assert.match(storeSource, /const MODE_COOKIE = 'themeMode'/);
	assert.match(storeSource, /const MATERIAL_COOKIE = 'materialTheme'/);
	assert.match(storeSource, /readCookie\(MODE_COOKIE\)/);
	assert.match(storeSource, /readCookie\(MATERIAL_COOKIE\)/);
	assert.match(storeSource, /writeCookie\(MODE_COOKIE, mode\)/);
	assert.match(storeSource, /writeCookie\(MATERIAL_COOKIE, material\)/);
	assert.doesNotMatch(storeSource, /localStorage|sessionStorage|fetch\(|navigator\.sendBeacon|WebSocket|EventSource/);
});
