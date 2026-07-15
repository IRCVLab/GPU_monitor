import { writable } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { readCookie, writeCookie } from '$lib/utils/cookies';
import type { DashboardView } from '$lib/utils/dashboardViewLabel';

export type { DashboardView } from '$lib/utils/dashboardViewLabel';
export type DashboardLayout = 'grid' | 'masonry';

const DASHBOARD_VIEW_COOKIE = 'dashboardView';
const DASHBOARD_LAYOUT_COOKIE = 'dashboardLayout';

export function readDashboardView(): DashboardView {
	const value = readCookie(DASHBOARD_VIEW_COOKIE);
	return value === 'compact' ? 'compact' : 'default';
}

export const dashboardView: Writable<DashboardView> = writable(readDashboardView());

dashboardView.subscribe((value) => {
	writeCookie(DASHBOARD_VIEW_COOKIE, value);
});

export function setDashboardView(value: DashboardView): void {
	dashboardView.set(value);
}

export function readDashboardLayout(): DashboardLayout {
	const value = readCookie(DASHBOARD_LAYOUT_COOKIE);
	return value === 'grid' ? 'grid' : 'masonry';
}

export const dashboardLayout: Writable<DashboardLayout> = writable(readDashboardLayout());

dashboardLayout.subscribe((value) => {
	writeCookie(DASHBOARD_LAYOUT_COOKIE, value);
});

export function setDashboardLayout(value: DashboardLayout): void {
	dashboardLayout.set(value);
}
