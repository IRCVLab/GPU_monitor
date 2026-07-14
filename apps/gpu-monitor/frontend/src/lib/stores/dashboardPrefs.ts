import { writable } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { readCookie, writeCookie } from '$lib/utils/cookies';

export type DashboardLayoutWidth = 'framed' | 'full';

const LAYOUT_WIDTH_COOKIE = 'dashboardLayoutWidth';

function readLayoutWidth(): DashboardLayoutWidth {
	const value = readCookie(LAYOUT_WIDTH_COOKIE);
	return value === 'full' ? 'full' : 'framed';
}

export const dashboardLayoutWidth: Writable<DashboardLayoutWidth> = writable(readLayoutWidth());

dashboardLayoutWidth.subscribe((value) => {
	writeCookie(LAYOUT_WIDTH_COOKIE, value);
});

export function setDashboardLayoutWidth(value: DashboardLayoutWidth): void {
	dashboardLayoutWidth.set(value);
}
