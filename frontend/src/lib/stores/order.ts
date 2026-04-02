import { writable } from 'svelte/store';
import { readCookie, writeCookie } from '$lib/utils/cookies';

const STORAGE_KEY = 'serverOrder';

function readOrder(): number[] {
	const raw = readCookie(STORAGE_KEY);
	if (!raw) return [];

	return raw
		.split(',')
		.map((value) => Number(value))
		.filter((value, index, list) => Number.isInteger(value) && value > 0 && list.indexOf(value) === index);
}

export const serverOrder = writable<number[]>(readOrder());

export async function saveOrder(ids: number[]): Promise<void> {
	const normalized = ids.filter(
		(value, index, list) => Number.isInteger(value) && value > 0 && list.indexOf(value) === index
	);

	serverOrder.set(normalized);
	writeCookie(STORAGE_KEY, normalized.join(','));
}
