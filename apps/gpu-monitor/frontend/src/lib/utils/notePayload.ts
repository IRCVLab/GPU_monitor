export type NoteKind = 'memo' | 'hold';

export interface CreateNoteInput {
	username: string;
	ssh_password: string;
	content: string;
	expires_at: string;
	kind?: NoteKind;
	gpu_indices?: number[];
}

export interface NoteCreatePayload {
	username: string;
	ssh_password: string;
	content: string;
	expires_at: string;
	kind: NoteKind;
	gpu_indices: number[];
}

function normalizeGpuIndices(indices: number[]): number[] {
	const normalized: number[] = [];
	for (const value of indices) {
		if (!Number.isInteger(value) || value < 0) {
			throw new Error('gpu_indices must contain non-negative integers');
		}
		normalized.push(value);
	}
	return [...new Set(normalized)].sort((a, b) => a - b);
}

export function buildNotePayload(input: CreateNoteInput): NoteCreatePayload {
	const kind = input.kind ?? 'memo';
	const gpuIndices = normalizeGpuIndices(input.gpu_indices ?? []);

	if (kind === 'memo' && gpuIndices.length > 0) {
		throw new Error('memo notes cannot include gpu indices');
	}
	if (kind === 'hold' && gpuIndices.length === 0) {
		throw new Error('hold notes require at least one gpu index');
	}

	return {
		username: input.username.trim(),
		ssh_password: input.ssh_password.trim(),
		content: input.content.trim(),
		expires_at: input.expires_at,
		kind,
		gpu_indices: gpuIndices
	};
}
