// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Script, createContext } from 'node:vm';

const storeSource = readFileSync(new URL('./theme.ts', import.meta.url), 'utf8');
const appHtml = readFileSync(new URL('../../app.html', import.meta.url), 'utf8');
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
	assert.match(storeSource, /Liquid Glass/);
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
