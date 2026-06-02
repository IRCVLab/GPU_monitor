// ─── API 응답 타입 ──────────────────────────────────────────────

export interface GpuInfo {
	index: number;
	name: string;
	utilization: number;    // 0-100 %
	memory_used: number;    // MB
	memory_total: number;   // MB
	temperature: number;    // °C
	power_draw: number;     // W
	users: string[];        // 사용 중인 유저명 목록
}

export interface SystemInfo {
	cpu_percent: number;
	ram_used: number;       // MB
	ram_total: number;      // MB
}

export interface StorageSummary {
	mount_count: number;
	total: number;
	used: number;
	percent: number;
}

export interface StorageMount {
	mount: string;
	device: string;
	fs_type: string;
	size: number;
	used: number;
	available: number;
	percent: number;
}

export interface StorageInfo {
	collected_at: string | null;
	summary: StorageSummary;
	mounts: StorageMount[];
}

export interface StatusReason {
	code: string;
	source: string;
	message: string;
	retryable: boolean;
	updated_at: string | null;
}

export type ServerStatus = 'online' | 'offline' | 'degraded' | 'unknown';

export interface ServerState {
	server_id: number;
	server_name: string;
	host: string;
	port?: number;
	network: 'internal' | 'external';
	status: ServerStatus;
	status_reason: StatusReason | null;
	last_seen: string | null;   // ISO timestamp
	gpus: GpuInfo[];
	system: SystemInfo | null;
	storage: StorageInfo | null;
	display_order?: number;
}

export interface ServerRecord {
	id: number;
	name: string;
	host: string;
	port: number;
	ssh_user: string;
	has_password: boolean;
	has_key: boolean;
	network: 'internal' | 'external';
	display_order: number;
	registered_by: string | null;
	created_at: string;
}

export interface Note {
	id: number;
	server_id: number;
	username: string;
	content: string;
	created_at: string;
	expires_at: string | null;
}

// ─── 이벤트 로그 ─────────────────────────────────────────────────

export type EventSeverity = 'info' | 'warning' | 'critical';

export interface EventLog {
	id: number;
	server_id: number | null;
	server_name: string | null;
	event_type: string;
	severity: EventSeverity;
	message: string;
	metadata: Record<string, unknown> | null;
	created_at: string;
}

// ─── WebSocket 메시지 ────────────────────────────────────────────

export interface WsMessage {
	type: 'update' | 'status_change';
	data: ServerState;
}
