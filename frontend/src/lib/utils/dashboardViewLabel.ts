export type DashboardView = 'default' | 'compact';

export function dashboardViewLabel(view: DashboardView): 'Full' | 'Compact' {
	return view === 'compact' ? 'Compact' : 'Full';
}
