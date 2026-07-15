// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('./app.css', import.meta.url), 'utf8');

function declarationsFor(selector) {
	const start = css.indexOf(`${selector} {`);
	assert.notEqual(start, -1, `missing ${selector} block`);
	const open = css.indexOf('{', start);
	let depth = 0;
	for (let index = open; index < css.length; index += 1) {
		if (css[index] === '{') depth += 1;
		if (css[index] === '}') depth -= 1;
		if (depth === 0) {
			const body = css.slice(open + 1, index);
			return Object.fromEntries(
				Array.from(body.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)).map(([, name, value]) => [
					`--${name}`,
					value.trim()
				])
			);
		}
	}
	assert.fail(`unterminated ${selector} block`);
}

function mergedDeclarations(...selectors) {
	return Object.assign({}, ...selectors.map((selector) => declarationsFor(selector)));
}

function channelToLinear(value) {
	return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex) {
	assert.match(hex, /^#[0-9a-f]{6}$/i, `expected six-digit hex, got ${hex}`);
	const normalized = hex.slice(1);
	const channels = [0, 2, 4].map((index) => Number.parseInt(normalized.slice(index, index + 2), 16) / 255);
	const [red, green, blue] = channels.map(channelToLinear);
	return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(a, b) {
	const [lighter, darker] = [relativeLuminance(a), relativeLuminance(b)].sort((left, right) => right - left);
	return (lighter + 0.05) / (darker + 0.05);
}

const darkTokens = {
	'--background': '#090b0f',
	'--foreground': '#f0f2f4',
	'--card': '#13161b',
	'--card-foreground': '#f0f2f4',
	'--popover': '#1a1d22',
	'--popover-foreground': '#f0f2f4',
	'--primary': '#3a8cff',
	'--primary-foreground': '#040609',
	'--secondary': '#1c2024',
	'--secondary-foreground': '#d9dfe5',
	'--muted': '#181b1f',
	'--muted-foreground': '#8f9aa4',
	'--accent': '#152946',
	'--accent-foreground': '#a5d0ff',
	'--destructive': '#ff515a',
	'--destructive-foreground': '#ffffff',
	'--border': '#26292e',
	'--input': '#26292e',
	'--ring': '#3a8cff',
	'--chart-1': '#3a8cff',
	'--chart-2': '#00b793',
	'--chart-3': '#9b61ea',
	'--chart-4': '#ff7527',
	'--chart-5': '#fb3a7f',
	'--sidebar': '#0f1216',
	'--sidebar-foreground': '#f0f2f4',
	'--sidebar-primary': '#3a8cff',
	'--sidebar-primary-foreground': '#040609',
	'--sidebar-accent': '#152946',
	'--sidebar-accent-foreground': '#a5d0ff',
	'--sidebar-border': '#212429',
	'--sidebar-ring': '#3a8cff',
	'--radius': '1.5rem',
	'--shadow-color': '#000000',
	'--shadow-opacity': '0.45',
	'--shadow-blur': '40px',
	'--shadow-spread': '0px',
	'--shadow-offset': '0 4px',
	'--spacing': '0.25rem',
	'--letter-spacing': '0em',
	'--shadow': '0 4px 40px rgb(0 0 0 / 0.45)',
	'--ops-on-primary': '#040609'
};

const lightTokens = {
	'--background': '#f4f5f7',
	'--foreground': '#0c121a',
	'--card': '#ffffff',
	'--card-foreground': '#0c121a',
	'--popover': '#ffffff',
	'--popover-foreground': '#0c121a',
	'--primary': '#297cef',
	'--primary-foreground': '#ffffff',
	'--secondary': '#e9ebee',
	'--secondary-foreground': '#222933',
	'--muted': '#eceff1',
	'--muted-foreground': '#565e69',
	'--accent': '#d9e6f9',
	'--accent-foreground': '#002c78',
	'--destructive': '#ee343b',
	'--destructive-foreground': '#ffffff',
	'--border': '#dbdee2',
	'--input': '#e2e5e8',
	'--ring': '#297cef',
	'--chart-1': '#297cef',
	'--chart-2': '#00a381',
	'--chart-3': '#864ad2',
	'--chart-4': '#f3680f',
	'--chart-5': '#ec2773',
	'--sidebar': '#eceff1',
	'--sidebar-foreground': '#0c121a',
	'--sidebar-primary': '#297cef',
	'--sidebar-primary-foreground': '#ffffff',
	'--sidebar-accent': '#d9e6f9',
	'--sidebar-accent-foreground': '#002c78',
	'--sidebar-border': '#dbdee2',
	'--sidebar-ring': '#297cef',
	'--radius': '1.5rem',
	'--shadow-color': '#4e5661',
	'--shadow-opacity': '0.10',
	'--shadow-blur': '28px',
	'--shadow-spread': '0px',
	'--shadow-offset': '0 2px',
	'--spacing': '0.25rem',
	'--letter-spacing': '0em',
	'--shadow': '0 2px 28px rgb(78 86 97 / 0.10)',
	'--ops-on-primary': '#040609'
};

const aliases = [
	'--surface', '--surface-foreground', '--surface-muted', '--surface-muted-foreground',
	'--surface-border', '--surface-ring', '--elevated', '--text', '--danger', '--success',
	'--warning', '--radius-outer', '--radius-inner', '--ops-bg', '--ops-fg', '--ops-card',
	'--ops-popover', '--ops-primary', '--ops-primary-fg', '--ops-on-primary', '--ops-secondary', '--ops-secondary-fg',
	'--ops-muted', '--ops-muted-fg', '--ops-border', '--ops-accent', '--ops-accent-fg',
	'--ops-danger', '--ops-danger-fg', '--ops-input', '--ops-ring', '--ops-shadow'
];

test(':root is the exact dark token block and html.dark duplicates it', () => {
	const root = declarationsFor(':root');
	const dark = declarationsFor('html.dark');
	assert.equal(css.match(/:root\s*\{[\s\S]*?color-scheme:\s*dark;/)?.[0].includes(':root'), true);
	for (const [name, value] of Object.entries(darkTokens)) {
		assert.equal(root[name], value, `:root ${name}`);
		assert.equal(dark[name], value, `html.dark ${name}`);
	}
	assert.equal(root['--shadow'], darkTokens['--shadow']);
	assert.equal(dark['--shadow'], darkTokens['--shadow']);
});

test('html.light has the exact light token block', () => {
	const light = declarationsFor('html.light');
	for (const [name, value] of Object.entries(lightTokens)) {
		assert.equal(light[name], value, `html.light ${name}`);
	}
});

test('theme blocks expose compatibility aliases and literal shadows', () => {
	for (const selector of [':root', 'html.dark', 'html.light']) {
		const declarations = declarationsFor(selector);
		for (const alias of aliases) assert.ok(alias in declarations, `${selector} missing ${alias}`);
		assert.doesNotMatch(declarations['--shadow'], /color-mix|var\(/, `${selector} shadow must be literal`);
	}
});

test('base liquid primary fill foreground stays AA in light and dark', () => {
	for (const scenario of [
		{ name: 'liquid light', selectors: ['html.light'], expected: '5.05' },
		{ name: 'liquid dark', selectors: ['html.dark'], expected: '6.16' }
	]) {
		const declarations = mergedDeclarations(...scenario.selectors);
		const ratio = contrastRatio(declarations['--primary'], declarations['--ops-on-primary']);
		assert.equal(ratio.toFixed(2), scenario.expected, `${scenario.name} contrast ratio`);
		assert.ok(ratio >= 4.5, `${scenario.name} must satisfy AA for small text`);
	}
});

const claudeLightTokens = {
	'--background': '#faf9f5',
	'--foreground': '#3d3929',
	'--card': '#f5f4ef',
	'--card-foreground': '#141413',
	'--popover': '#ffffff',
	'--popover-foreground': '#28261b',
	'--primary': '#c96442',
	'--primary-foreground': '#ffffff',
	'--secondary': '#e9e6dc',
	'--secondary-foreground': '#535146',
	'--muted': '#ede9de',
	'--muted-foreground': '#6e6d68',
	'--accent': '#e9e6dc',
	'--accent-foreground': '#28261b',
	'--destructive': '#141413',
	'--destructive-foreground': '#ffffff',
	'--border': '#dad9d4',
	'--input': '#b4b2a7',
	'--ring': '#c96442',
	'--chart-1': '#b05730',
	'--chart-2': '#9c87f5',
	'--chart-3': '#ded8c4',
	'--chart-4': '#dbd3f0',
	'--chart-5': '#b4552d',
	'--radius': '1rem'
};

const claudeDarkTokens = {
	'--background': '#262624',
	'--foreground': '#f1f1ef',
	'--card': '#2c2c2b',
	'--card-foreground': '#faf9f5',
	'--popover': '#30302e',
	'--popover-foreground': '#e5e5e2',
	'--primary': '#d97757',
	'--primary-foreground': '#141413',
	'--secondary': '#faf9f5',
	'--secondary-foreground': '#30302e',
	'--muted': '#1b1b19',
	'--muted-foreground': '#b7b5a9',
	'--accent': '#1a1915',
	'--accent-foreground': '#f5f4ee',
	'--destructive': '#ef4444',
	'--destructive-foreground': '#ffffff',
	'--border': '#3e3e38',
	'--input': '#52514a',
	'--ring': '#d97757',
	'--chart-1': '#b05730',
	'--chart-2': '#9c87f5',
	'--chart-3': '#1a1915',
	'--chart-4': '#2f2b48',
	'--chart-5': '#b4552d',
	'--radius': '1rem'
};

const astroLightTokens = {
	'--background': '#e8ebed',
	'--foreground': '#333333',
	'--card': '#ffffff',
	'--card-foreground': '#333333',
	'--popover': '#ffffff',
	'--popover-foreground': '#333333',
	'--primary': '#df6035',
	'--primary-foreground': '#ffffff',
	'--secondary': '#2f4b79',
	'--secondary-foreground': '#ffffff',
	'--muted': '#f9fafb',
	'--muted-foreground': '#6b7280',
	'--accent': '#d6e4f0',
	'--accent-foreground': '#1e3a8a',
	'--destructive': '#ef4444',
	'--destructive-foreground': '#ffffff',
	'--border': '#cccccc',
	'--input': '#f4f5f7',
	'--ring': '#e05d38',
	'--chart-1': '#7399bf',
	'--chart-2': '#e16f41',
	'--chart-3': '#d54450',
	'--chart-4': '#e2b146',
	'--chart-5': '#3c4c76',
	'--radius': '.5rem'
};

const astroDarkTokens = {
	'--background': '#1a1a1a',
	'--foreground': '#e5e5e5',
	'--card': '#202020',
	'--card-foreground': '#e5e5e5',
	'--popover': '#202020',
	'--popover-foreground': '#e5e5e5',
	'--primary': '#df6035',
	'--primary-foreground': '#ffffff',
	'--secondary': '#284167',
	'--secondary-foreground': '#e5e5e5',
	'--muted': '#2a2a2a',
	'--muted-foreground': '#808080',
	'--accent': '#2a3656',
	'--accent-foreground': '#bfdbfe',
	'--destructive': '#ef4444',
	'--destructive-foreground': '#ffffff',
	'--border': '#353535',
	'--input': '#303030',
	'--ring': '#e05d38',
	'--chart-1': '#85a6c7',
	'--chart-2': '#e16f41',
	'--chart-3': '#d54450',
	'--chart-4': '#e2b146',
	'--chart-5': '#3c4c76',
	'--radius': '.5rem'
};

function assertTokenSet(selector, expected) {
	const declarations = declarationsFor(selector);
	for (const [name, value] of Object.entries(expected)) {
		assert.equal(declarations[name], value, `${selector} ${name}`);
	}
}

test('Task 5 material presets expose exact Claude+ and AstroVista light/dark semantic token blocks', () => {
	assertTokenSet("html.light[data-material='claude']", claudeLightTokens);
	assertTokenSet("html.dark[data-material='claude']", claudeDarkTokens);
	assertTokenSet("html.light[data-material='astro']", astroLightTokens);
	assertTokenSet("html.dark[data-material='astro']", astroDarkTokens);
});

test('Task 5 material presets centralize functional-layer material variables', () => {
	const liquid = declarationsFor("html[data-material='liquid']");
	assert.equal(liquid['--material-surface-alpha'], '0.72');
	assert.equal(liquid['--material-surface-mix'], '72%');
	assert.equal(liquid['--material-blur'], '24px');
	assert.equal(liquid['--material-saturation'], '145%');
	assert.equal(liquid['--material-radius'], '24px');
	assert.ok(liquid['--material-shadow'].includes('color-mix(in srgb, var(--ops-primary)'), 'liquid shadow is tinted by semantic primary');

	const claude = declarationsFor("html[data-material='claude']");
	assert.equal(claude['--material-blur'], '14px');
	assert.equal(claude['--material-saturation'], '112%');
	assert.equal(claude['--material-radius'], '1rem');

	const astro = declarationsFor("html[data-material='astro']");
	assert.equal(astro['--material-blur'], '18px');
	assert.equal(astro['--material-saturation'], '128%');
	assert.equal(astro['--material-radius'], '.75rem');
});

test('Task 5 removes obsolete accent color selectors from global CSS', () => {
	assert.doesNotMatch(css, /data-color-theme/);
	assert.doesNotMatch(css, /html\.rose/);
});
