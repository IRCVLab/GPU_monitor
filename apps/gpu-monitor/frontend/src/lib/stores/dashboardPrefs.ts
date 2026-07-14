import { writable } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { readCookie, writeCookie } from '$lib/utils/cookies';
import type { DashboardView } from '$lib/utils/dashboardViewLabel';

export type { DashboardView } from '$lib/utils/dashboardViewLabel';

const DASHBOARD_VIEW_COOKIE = 'dashboardView';

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
