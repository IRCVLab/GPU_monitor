// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Script, createContext } from 'node:vm';

const storeSource = readFileSync(new URL('./theme.ts', import.meta.url), 'utf8');
const appHtml = readFileSync(new URL('../../app.html', import.meta.url), 'utf8');

test('Task 5 theme store exposes semantic material presets only', () => {
	assert.match(storeSource, /materialThemes = \['liquid', 'claude', 'astro'\] as const/);
	assert.match(storeSource, /materialThemeOptions = \[/);
	assert.match(storeSource, /Liquid Glass/);
	assert.match(storeSource, /Claude\+/);
	assert.match(storeSource, /AstroVista/);
	assert.doesNotMatch(storeSource, /colorThemes|colorThemeOptions|setColorTheme/);
});

test('Task 5 store and initial HTML migrate old accent values to liquid with matching logic', () => {
	for (const legacy of ['blue', 'violet', 'emerald', 'rose', 'pink']) {
		assert.match(storeSource, new RegExp(`oldMaterialValues[\\s\\S]*['\"]${legacy}['\"]`), `store migrates ${legacy}`);
		assert.match(appHtml, new RegExp(`oldMaterialValues[\\s\\S]*['\"]${legacy}['\"]`), `app.html migrates ${legacy}`);
	}
	assert.match(storeSource, /const MATERIAL_COOKIE = 'materialTheme'/);
	assert.match(storeSource, /const LEGACY_COLOR_COOKIE = 'colorTheme'/);
	assert.match(appHtml, /read\('materialTheme'\)/);
	assert.match(appHtml, /read\('colorTheme'\)/);
	assert.match(storeSource, /oldMaterialValues\.includes[\s\S]*return 'liquid'/);
	assert.match(appHtml, /oldMaterialValues\.includes\(requestedMaterial\) \? 'liquid' : 'liquid'/);
});

test('Task 5 app.html is SSR-safe and initializes the semantic material data attribute', () => {
	assert.match(appHtml, /<html lang="ko" class="dark" data-material="liquid">/);
	assert.match(appHtml, /document\.documentElement\.dataset\.material = material/);
	assert.doesNotMatch(appHtml, /data-color-theme|dataset\.colorTheme|colorTheme =/);
});


test('Task 5 app.html inline cookie reader finds material cookies after earlier cookies', () => {
	const scriptBody = appHtml.match(/<script>\s*([\s\S]*?)\s*<\/script>/)?.[1];
	assert.ok(scriptBody, 'app.html inline theme script must exist');
	const classes = new Set(['dark']);
	const context = createContext({
		document: {
			cookie: 'session=abc; themeMode=light; unrelated=1; materialTheme=astro',
			documentElement: {
				classList: {
					remove: (...values) => values.forEach((value) => classes.delete(value)),
					add: (value) => classes.add(value)
				},
				dataset: {}
			}
		}
	});

	new Script(scriptBody).runInContext(context);
	assert.equal(context.document.documentElement.dataset.material, 'astro');
	assert.equal(classes.has('light'), true);
	assert.equal(classes.has('dark'), false);
});
