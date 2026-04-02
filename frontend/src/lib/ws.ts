import { writable } from 'svelte/store';
import type { ServerState, WsMessage } from '$lib/types';
import { isServerStateEqual, normalizeServerState, serverStates } from '$lib/stores/servers';

export const wsConnected = writable(false);

let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let manualClose = false;

function getWsUrl(): string {
	const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
	return `${protocol}//${window.location.host}/ws/metrics`;
}

function hasFinitePort(value: unknown): value is number {
	return typeof value === 'number' && Number.isFinite(value);
}

function mergeIncomingState(
	existing: ServerState | undefined,
	incoming: ServerState,
	raw: Record<string, unknown>
): ServerState {
	if (!existing) return incoming;

	return {
		...existing,
		...incoming,
		server_name:
			typeof raw.server_name === 'string' ? incoming.server_name : existing.server_name,
		host: typeof raw.host === 'string' ? incoming.host : existing.host,
		port: hasFinitePort(raw.port) ? incoming.port : existing.port,
		network: typeof raw.network === 'string' ? incoming.network : existing.network,
		display_order:
			typeof raw.display_order === 'number' && Number.isFinite(raw.display_order)
				? incoming.display_order
				: existing.display_order
	};
}

function applyWsMessage(message: WsMessage): void {
	if (message.type !== 'update' && message.type !== 'status_change') {
		return;
	}

	const raw =
		message.data && typeof message.data === 'object'
			? (message.data as unknown as Record<string, unknown>)
			: null;
	if (!raw) return;

	const normalized = normalizeServerState(raw);
	if (!normalized) return;

	serverStates.update((current) => {
		const existing = current.get(normalized.server_id);
		const nextState = mergeIncomingState(existing, normalized, raw);
		if (existing && isServerStateEqual(existing, nextState)) {
			return current;
		}

		const next = new Map(current);
		next.set(nextState.server_id, nextState);
		return next;
	});
}

function connect(): void {
	if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
		return;
	}

	try {
		socket = new WebSocket(getWsUrl());
	} catch {
		wsConnected.set(false);
		socket = null;
		if (!manualClose) {
			reconnectTimer = setTimeout(connect, 3000);
		}
		return;
	}

	socket.addEventListener('open', () => {
		wsConnected.set(true);
	});

	socket.addEventListener('message', (event: MessageEvent) => {
		let msg: WsMessage | Record<string, unknown>;
		try {
			msg = JSON.parse(event.data as string) as WsMessage | Record<string, unknown>;
		} catch {
			return;
		}

		if (
			typeof msg !== 'object' ||
			msg === null ||
			!('type' in msg) ||
			!('data' in msg)
		) {
			return;
		}

		applyWsMessage(msg as WsMessage);
	});

	socket.addEventListener('close', () => {
		wsConnected.set(false);
		socket = null;
		if (!manualClose) {
			reconnectTimer = setTimeout(connect, 3000);
		}
	});

	socket.addEventListener('error', () => {
		// Let the close handler schedule reconnect
		socket?.close();
	});
}

export function connectWs(): void {
	manualClose = false;
	if (reconnectTimer !== null) {
		clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}
	connect();
}

export function disconnectWs(): void {
	manualClose = true;
	if (reconnectTimer !== null) {
		clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}
	if (socket) {
		socket.close();
		socket = null;
	}
	wsConnected.set(false);
}
