import { browser } from '$app/environment';
import { writable } from 'svelte/store';
import type { Writable } from 'svelte/store';

import { DEV_SCENARIOS, isDevScenario, type DevScenario } from '$lib/utils/devScenario';

const DEV_SCENARIO_STORAGE_KEY = 'monitoring-dev-scenario';
const DEV_SCENARIO_ENABLED = import.meta.env.DEV;
const DEFAULT_DEV_SCENARIO: DevScenario = DEV_SCENARIOS[0];

function readDevScenario(): DevScenario {
	if (!DEV_SCENARIO_ENABLED || !browser) return DEFAULT_DEV_SCENARIO;
	const value = sessionStorage.getItem(DEV_SCENARIO_STORAGE_KEY);
	return isDevScenario(value) ? value : DEFAULT_DEV_SCENARIO;
}

const devScenarioStore: Writable<DevScenario> = writable(readDevScenario());

if (DEV_SCENARIO_ENABLED && browser) {
	devScenarioStore.subscribe((value) => {
		if (value === DEFAULT_DEV_SCENARIO) {
			sessionStorage.removeItem(DEV_SCENARIO_STORAGE_KEY);
			return;
		}

		sessionStorage.setItem(DEV_SCENARIO_STORAGE_KEY, value);
	});
}

export const activeDevScenario = {
	subscribe: devScenarioStore.subscribe
};

export function setDevScenario(value: DevScenario): void {
	if (!DEV_SCENARIO_ENABLED) return;
	devScenarioStore.set(value);
}

export function resetDevScenario(): void {
	if (!DEV_SCENARIO_ENABLED) return;
	devScenarioStore.set(DEFAULT_DEV_SCENARIO);
}
