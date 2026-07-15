import type { ServerRecord, ServerState } from '../types';

function catalogFallback(record: ServerRecord, existing?: ServerState): ServerState {
	return {
		server_id: record.id,
		server_name: record.name,
		host: record.host,
		port: record.port,
		network: record.network,
		status: existing?.status ?? 'unknown',
		status_reason: existing?.status_reason ?? null,
		last_seen: existing?.last_seen ?? null,
		gpus: existing?.gpus ?? [],
		system: existing?.system ?? null,
		storage: existing?.storage ?? null,
		display_order: record.display_order
	};
}

export function mergeServerRecordState(
	record: ServerRecord,
	status: ServerState | null,
	existing?: ServerState
): ServerState {
	const fallback = catalogFallback(record, existing);
	if (!status) return fallback;

	return {
		...fallback,
		...status,
		server_id: record.id,
		server_name: record.name,
		host: record.host,
		port: record.port,
		network: record.network,
		display_order: record.display_order
	};
}
