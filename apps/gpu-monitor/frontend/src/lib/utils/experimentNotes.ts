export interface ExperimentNote {
	id: string;
	summary: string;
	gpu_indices: number[];
	eta_seconds: number | null;
	observed_at: string;
}

export interface ExperimentNotes {
	enabled: boolean;
	configured: boolean;
	by_server: Record<string, ExperimentNote[]>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isGpuIndex(value: unknown): value is number {
	return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function isExperimentNote(value: unknown): value is ExperimentNote {
	return isRecord(value)
		&& typeof value.id === 'string'
		&& typeof value.summary === 'string'
		&& Array.isArray(value.gpu_indices)
		&& value.gpu_indices.every(isGpuIndex)
		&& (value.eta_seconds === null
			|| (typeof value.eta_seconds === 'number' && Number.isFinite(value.eta_seconds) && value.eta_seconds >= 0))
		&& typeof value.observed_at === 'string'
		&& Number.isFinite(Date.parse(value.observed_at));
}

export function parseExperimentNotes(value: unknown): ExperimentNotes {
	if (!isRecord(value) || typeof value.enabled !== 'boolean' || typeof value.configured !== 'boolean'
		|| !isRecord(value.by_server)) {
		throw new Error('자동 실험 메모 응답 형식이 올바르지 않습니다.');
	}
	const { enabled, configured } = value;
	const by_server: Record<string, ExperimentNote[]> = {};
	if (enabled && configured) {
		for (const [serverId, notes] of Object.entries(value.by_server)) {
			if (!/^[1-9][0-9]*$/.test(serverId) || !Array.isArray(notes) || !notes.every(isExperimentNote)) {
				throw new Error('자동 실험 메모 응답 형식이 올바르지 않습니다.');
			}
			by_server[serverId] = notes;
		}
	}
	return { enabled, configured, by_server };
}

const etaTime = new Intl.DateTimeFormat('ko-KR', {
	month: 'numeric',
	day: 'numeric',
	hour: '2-digit',
	minute: '2-digit',
	hourCycle: 'h23'
});

export function experimentEtaLabel({ eta_seconds, observed_at }: ExperimentNote, nowMs: number): string {
	if (eta_seconds === null) return '종료 예상 미확인';
	const end = new Date(Date.parse(observed_at) + eta_seconds * 1000);
	if (!(end.getTime() > nowMs)) return '종료 예상 미확인';
	return `종료 예상 ${etaTime.format(end)}`;
}
