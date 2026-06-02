import type { EventLog, Note, ServerRecord, ServerState } from '$lib/types';

const BASE = '/api';
const DEFAULT_TIMEOUT_MS = 15000;
const DASHBOARD_TIMEOUT_MS = 5000;

interface RegisterServerPayload {
	name: string;
	host: string;
	port: number;
	ssh_user: string;
	ssh_password?: string;
	ssh_private_key?: string;
	network: 'internal' | 'external';
}

interface RequestOptions {
	signal?: AbortSignal;
}

async function fetchWithTimeout(
	input: RequestInfo | URL,
	init?: RequestInit,
	timeoutMs = DEFAULT_TIMEOUT_MS,
	options?: RequestOptions
): Promise<Response> {
	const controller = new AbortController();
	const upstreamSignal = options?.signal;
	const timeout = setTimeout(() => controller.abort(), timeoutMs);
	const abortFromUpstream = () => controller.abort();

	if (upstreamSignal) {
		if (upstreamSignal.aborted) {
			controller.abort();
		} else {
			upstreamSignal.addEventListener('abort', abortFromUpstream, { once: true });
		}
	}

	try {
		return await fetch(input, {
			...init,
			signal: controller.signal
		});
	} catch (error) {
		if (upstreamSignal?.aborted) {
			throw error;
		}
		if (error instanceof DOMException && error.name === 'AbortError') {
			throw new Error(`요청 시간이 초과되었습니다. (${Math.ceil(timeoutMs / 1000)}초)`);
		}
		throw error;
	} finally {
		clearTimeout(timeout);
		upstreamSignal?.removeEventListener('abort', abortFromUpstream);
	}
}

async function handleResponse<T>(res: Response): Promise<T> {
	if (!res.ok) {
		let message = `HTTP ${res.status}`;
		try {
			const body = await res.json();
			if (body.detail) {
				message = String(body.detail);
				if (message === 'Invalid admin password') {
					message =
						'관리자 패스워드가 올바르지 않습니다. `monitoring_v2/.env`의 `ADMIN_PASSWORD` 값을 확인하세요.';
				}
			}
		} catch {
			// ignore parse error, use status message
		}
		throw new Error(message);
	}

	if (res.status === 204 || res.status === 205) {
		return undefined as T;
	}

	const text = await res.text();
	if (!text) {
		return undefined as T;
	}

	return JSON.parse(text) as T;
}

export async function getServers(): Promise<ServerRecord[]> {
	const res = await fetchWithTimeout(`${BASE}/servers`, undefined, DASHBOARD_TIMEOUT_MS);
	return handleResponse<ServerRecord[]>(res);
}

export async function getServersWithOptions(options?: RequestOptions): Promise<ServerRecord[]> {
	const res = await fetchWithTimeout(`${BASE}/servers`, undefined, DASHBOARD_TIMEOUT_MS, options);
	return handleResponse<ServerRecord[]>(res);
}

export async function getServerStatus(): Promise<Record<number, ServerState>> {
	const res = await fetchWithTimeout(`${BASE}/servers/status`, undefined, DASHBOARD_TIMEOUT_MS);
	return handleResponse<Record<number, ServerState>>(res);
}

export async function getNotes(serverId: number): Promise<Note[]> {
	const res = await fetchWithTimeout(`${BASE}/servers/${serverId}/notes`);
	return handleResponse<Note[]>(res);
}

export async function createNote(
	serverId: number,
	username: string,
	sshPassword: string,
	content: string,
	expiresAt: string
): Promise<Note> {
	const res = await fetchWithTimeout(`${BASE}/servers/${serverId}/notes`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			username,
			ssh_password: sshPassword,
			content,
			expires_at: expiresAt
		})
	});
	return handleResponse<Note>(res);
}

export async function deleteNote(
	serverId: number,
	noteId: number,
	username: string,
	sshPassword: string,
	adminPassword?: string
): Promise<void> {
	const body: Record<string, string> = { username, ssh_password: sshPassword };
	if (adminPassword !== undefined) body.admin_password = adminPassword;
	const res = await fetchWithTimeout(`${BASE}/servers/${serverId}/notes/${noteId}`, {
		method: 'DELETE',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
	await handleResponse<unknown>(res);
}

export async function registerServer(
	data: RegisterServerPayload,
	adminPassword: string
): Promise<ServerRecord> {
	const res = await fetchWithTimeout(`${BASE}/servers`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...data, admin_password: adminPassword })
	});
	return handleResponse<ServerRecord>(res);
}

export async function deleteServer(serverId: number, adminPassword: string): Promise<void> {
	const res = await fetchWithTimeout(`${BASE}/servers/${serverId}`, {
		method: 'DELETE',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ admin_password: adminPassword })
	});
	await handleResponse<unknown>(res);
}

export async function updateServer(
	serverId: number,
	data: RegisterServerPayload,
	adminPassword: string
): Promise<ServerRecord> {
	const res = await fetchWithTimeout(`${BASE}/servers/${serverId}`, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...data, admin_password: adminPassword })
	});
	return handleResponse<ServerRecord>(res);
}

export async function testConnection(
	serverId: number,
	adminPassword: string
): Promise<{ ok: boolean; reason?: string }> {
	const res = await fetchWithTimeout(`${BASE}/servers/${serverId}/test`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ admin_password: adminPassword })
	});
	return handleResponse<{ ok: boolean; reason?: string }>(res);
}

export async function testConnectionRaw(params: {
	server_id?: number;
	admin_password?: string;
	host: string;
	port: number;
	ssh_user: string;
	ssh_password?: string;
	ssh_private_key?: string;
}): Promise<{ ok: boolean; reason?: string }> {
	const res = await fetchWithTimeout(`${BASE}/servers/test-connection`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(params)
	});
	return handleResponse<{ ok: boolean; reason?: string }>(res);
}

export async function getLogs(params?: {
	server_id?: number;
	event_type?: string;
	severity?: string;
	limit?: number;
	offset?: number;
}, options?: RequestOptions): Promise<{ items: EventLog[]; total: number }> {
	const q = new URLSearchParams();
	if (params?.server_id !== undefined) q.set('server_id', String(params.server_id));
	if (params?.event_type) q.set('event_type', params.event_type);
	if (params?.severity) q.set('severity', params.severity);
	if (params?.limit !== undefined) q.set('limit', String(params.limit));
	if (params?.offset !== undefined) q.set('offset', String(params.offset));
	const qs = q.toString();
	const res = await fetchWithTimeout(`${BASE}/logs${qs ? `?${qs}` : ''}`, undefined, DEFAULT_TIMEOUT_MS, options);
	return handleResponse<{ items: EventLog[]; total: number }>(res);
}

export async function getLogEventTypes(options?: RequestOptions): Promise<string[]> {
	const res = await fetchWithTimeout(`${BASE}/logs/event-types`, undefined, DEFAULT_TIMEOUT_MS, options);
	return handleResponse<string[]>(res);
}
