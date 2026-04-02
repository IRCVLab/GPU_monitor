import { writable } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { readCookie, writeCookie } from '$lib/utils/cookies';

export type DashboardTextScale = 'small' | 'default' | 'large';
export type DashboardLayoutWidth = 'framed' | 'full';

const TEXT_SCALE_COOKIE = 'dashboardTextScale';
const TEXT_SCALE_VERSION_COOKIE = 'dashboardTextScaleVersion';
const LAYOUT_WIDTH_COOKIE = 'dashboardLayoutWidth';
const TEXT_SCALE_VERSION = '2';

function readTextScale(): DashboardTextScale {
	const value = readCookie(TEXT_SCALE_COOKIE);
	const version = readCookie(TEXT_SCALE_VERSION_COOKIE);

	if (version !== TEXT_SCALE_VERSION) {
		if (value === 'default') return 'small';
		if (value === 'large') return 'default';
	}

	if (value === 'small' || value === 'default' || value === 'large') {
		return value;
	}

	return 'default';
}

function readLayoutWidth(): DashboardLayoutWidth {
	const value = readCookie(LAYOUT_WIDTH_COOKIE);
	return value === 'full' ? 'full' : 'framed';
}

export const dashboardTextScale: Writable<DashboardTextScale> = writable(readTextScale());
export const dashboardLayoutWidth: Writable<DashboardLayoutWidth> = writable(readLayoutWidth());

dashboardTextScale.subscribe((value) => {
	writeCookie(TEXT_SCALE_COOKIE, value);
	writeCookie(TEXT_SCALE_VERSION_COOKIE, TEXT_SCALE_VERSION);
});

dashboardLayoutWidth.subscribe((value) => {
	writeCookie(LAYOUT_WIDTH_COOKIE, value);
});

export function setDashboardTextScale(value: DashboardTextScale): void {
	dashboardTextScale.set(value);
}

export function setDashboardLayoutWidth(value: DashboardLayoutWidth): void {
	dashboardLayoutWidth.set(value);
}
