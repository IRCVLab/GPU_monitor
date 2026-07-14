import { writable } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { readCookie, writeCookie } from '$lib/utils/cookies';

export const themeModes = ['dark', 'light'] as const;
export const colorThemes = ['blue', 'violet', 'emerald'] as const;
export type ThemeMode = (typeof themeModes)[number];
export type ColorTheme = (typeof colorThemes)[number];

export const colorThemeOptions = [
	{ value: 'blue', label: 'Blue', color: '#297cef' },
	{ value: 'violet', label: 'Violet', color: '#864ad2' },
	{ value: 'emerald', label: 'Emerald', color: '#00a381' }
] as const satisfies ReadonlyArray<{ value: ColorTheme; label: string; color: string }>;

const MODE_COOKIE = 'themeMode';
const COLOR_COOKIE = 'colorTheme';
const LEGACY_THEME_COOKIE = 'theme';

function readMode(): ThemeMode {
	const direct = (readCookie(MODE_COOKIE) ?? '').toLowerCase();
	if (direct === 'light' || direct === 'dark') return direct;
	const legacy = (readCookie(LEGACY_THEME_COOKIE) ?? '').toLowerCase();
	return legacy === 'light' || legacy === 'rose' || legacy === 'pink' ? 'light' : 'dark';
}

function readColor(): ColorTheme {
	const direct = (readCookie(COLOR_COOKIE) ?? '').toLowerCase();
	if (colorThemes.includes(direct as ColorTheme)) return direct as ColorTheme;
	const legacy = (readCookie(LEGACY_THEME_COOKIE) ?? '').toLowerCase();
	return legacy === 'rose' || legacy === 'pink' ? 'violet' : 'blue';
}

function applyTheme(mode: ThemeMode, color: ColorTheme): void {
	if (typeof document === 'undefined') return;
	const classes = document.documentElement.classList;
	classes.remove(...themeModes, 'rose');
	classes.add(mode);
	document.documentElement.dataset.colorTheme = color;
}

export const themeMode: Writable<ThemeMode> = writable(readMode());
export const colorTheme: Writable<ColorTheme> = writable(readColor());

function persistAndApply(): void {
	let mode: ThemeMode = 'dark';
	let color: ColorTheme = 'blue';
	themeMode.subscribe((value) => (mode = value))();
	colorTheme.subscribe((value) => (color = value))();
	applyTheme(mode, color);
	writeCookie(MODE_COOKIE, mode);
	writeCookie(COLOR_COOKIE, color);
}

themeMode.subscribe(() => persistAndApply());
colorTheme.subscribe(() => persistAndApply());

export function setThemeMode(value: ThemeMode): void { themeMode.set(value); }
export function toggleThemeMode(): void { themeMode.update((mode) => (mode === 'dark' ? 'light' : 'dark')); }
export function setColorTheme(value: ColorTheme): void { colorTheme.set(value); }
