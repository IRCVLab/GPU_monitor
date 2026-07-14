// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('./app.css', import.meta.url), 'utf8');

function declarationsFor(selector) {
	const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = css.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\n\\t\\}`, 'm'));
	assert.ok(match, `missing ${selector} block`);
	return Object.fromEntries(
		Array.from(match[1].matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)).map(([, name, value]) => [
			`--${name}`,
			value.trim()
		])
	);
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
	'--shadow': '0 4px 40px rgb(0 0 0 / 0.45)'
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
	'--shadow': '0 2px 28px rgb(78 86 97 / 0.10)'
};

const aliases = [
	'--surface', '--surface-foreground', '--surface-muted', '--surface-muted-foreground',
	'--surface-border', '--surface-ring', '--elevated', '--text', '--danger', '--success',
	'--warning', '--radius-outer', '--radius-inner', '--ops-bg', '--ops-fg', '--ops-card',
	'--ops-popover', '--ops-primary', '--ops-primary-fg', '--ops-secondary', '--ops-secondary-fg',
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
