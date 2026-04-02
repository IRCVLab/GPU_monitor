import { writable } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { readCookie, writeCookie } from '$lib/utils/cookies';

export const themeValues = ['dark', 'light', 'rose'] as const;

export type Theme = (typeof themeValues)[number];

const THEME_COOKIE = 'theme';
const legacyThemeAliases: Record<string, Theme> = {
	pink: 'rose'
};

function isTheme(value: string): value is Theme {
	return themeValues.includes(value as Theme);
}

function readThemeCookie(): { theme: Theme; persist: boolean } {
	const rawValue = readCookie(THEME_COOKIE);
	if (!rawValue) return { theme: 'dark', persist: false };

	const value = rawValue.toLowerCase();
	if (isTheme(value)) {
		return { theme: value, persist: false };
	}

	const aliasedTheme = legacyThemeAliases[value];
	if (aliasedTheme) {
		return { theme: aliasedTheme, persist: true };
	}

	return { theme: 'dark', persist: false };
}

function applyTheme(value: Theme, persist = true): void {
	if (typeof document === 'undefined') return;

	const classes = document.documentElement.classList;
	classes.remove(...themeValues);
	classes.add(value);

	if (persist) {
		writeCookie(THEME_COOKIE, value);
	}
}

const initial = readThemeCookie();
let persistNextUpdate = initial.persist;

export const theme: Writable<Theme> = writable(initial.theme);

theme.subscribe((value) => {
	applyTheme(value, persistNextUpdate);
	persistNextUpdate = true;
});

export function setTheme(value: Theme): void {
	theme.set(value);
}

export function toggleTheme(): void {
	theme.update((current) => {
		const currentIndex = themeValues.indexOf(current);
		const nextIndex = (currentIndex + 1) % themeValues.length;
		return themeValues[nextIndex];
	});
}
