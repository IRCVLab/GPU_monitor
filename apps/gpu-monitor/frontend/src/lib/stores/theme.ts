import { writable } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { readCookie, writeCookie } from '$lib/utils/cookies';

export const themeModes = ['dark', 'light'] as const;
export const materialThemes = ['liquid', 'claude', 'astro'] as const;
export type ThemeMode = (typeof themeModes)[number];
export type MaterialTheme = (typeof materialThemes)[number];

export const materialThemeOptions = [
	{ value: 'liquid', label: 'Liquid Glass', description: 'Cool translucent glass' },
	{ value: 'claude', label: 'Claude+', description: 'Warm paper-soft material' },
	{ value: 'astro', label: 'AstroVista', description: 'Cool crisp depth' }
] as const satisfies ReadonlyArray<{ value: MaterialTheme; label: string; description: string }>;

const MODE_COOKIE = 'themeMode';
const MATERIAL_COOKIE = 'materialTheme';
const LEGACY_COLOR_COOKIE = 'colorTheme';
const LEGACY_THEME_COOKIE = 'theme';

function normalizeMode(value: string): ThemeMode | null {
	return value === 'light' || value === 'dark' ? value : null;
}

function normalizeMaterial(value: string): MaterialTheme | null {
	return materialThemes.includes(value as MaterialTheme) ? (value as MaterialTheme) : null;
}

function readMode(): ThemeMode {
	const direct = normalizeMode((readCookie(MODE_COOKIE) ?? '').toLowerCase());
	if (direct) return direct;
	const legacy = (readCookie(LEGACY_THEME_COOKIE) ?? '').toLowerCase();
	return legacy === 'light' || legacy === 'rose' || legacy === 'pink' ? 'light' : 'dark';
}

function readMaterial(): MaterialTheme {
	const requestedMaterial = (
		readCookie(MATERIAL_COOKIE) ??
		readCookie(LEGACY_COLOR_COOKIE) ??
		readCookie(LEGACY_THEME_COOKIE) ??
		''
	).toLowerCase();
	return normalizeMaterial(requestedMaterial) ?? 'liquid';
}

function applyTheme(mode: ThemeMode, material: MaterialTheme): void {
	if (typeof document === 'undefined') return;
	const classes = document.documentElement.classList;
	classes.remove(...themeModes, 'rose');
	classes.add(mode);
	document.documentElement.dataset.material = material;
	delete document.documentElement.dataset.colorTheme;
}

export const themeMode: Writable<ThemeMode> = writable(readMode());
export const materialTheme: Writable<MaterialTheme> = writable(readMaterial());

function persistAndApply(): void {
	let mode: ThemeMode = 'dark';
	let material: MaterialTheme = 'liquid';
	themeMode.subscribe((value) => (mode = value))();
	materialTheme.subscribe((value) => (material = value))();
	applyTheme(mode, material);
	writeCookie(MODE_COOKIE, mode);
	writeCookie(MATERIAL_COOKIE, material);
}

themeMode.subscribe(() => persistAndApply());
materialTheme.subscribe(() => persistAndApply());

export function setThemeMode(value: ThemeMode): void { themeMode.set(value); }
export function toggleThemeMode(): void { themeMode.update((mode) => (mode === 'dark' ? 'light' : 'dark')); }
export function setMaterialTheme(value: MaterialTheme): void { materialTheme.set(value); }
