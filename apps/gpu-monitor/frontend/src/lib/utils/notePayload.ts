import type { NotePriority } from '../types.ts';

export type NoteKind = 'memo' | 'hold';

export interface CreateNoteInput {
	username: string;
	display_name?: string | null;
	priority?: NotePriority | null;
	ssh_password: string;
	content: string;
	expires_at: string;
	kind?: NoteKind;
	gpu_indices?: number[];
}

export interface NoteCreatePayload {
	username: string;
	display_name: string | null;
	priority: NotePriority;
	ssh_password: string;
	content: string;
	expires_at: string;
	kind: NoteKind;
	gpu_indices: number[];
}

const NOTE_PRIORITIES = ['normal', 'high', 'urgent'] as const satisfies readonly NotePriority[];

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

export function normalizeNoteDisplayName(value: string | null | undefined): string | null {
	if (value === null || value === undefined) return null;
	if (typeof value !== 'string') {
		throw new Error('display_name must be a string');
	}
	const trimmed = value.trim();
	if (!trimmed) return null;
	if (trimmed.length > 40) {
		throw new Error('display_name must be at most 40 characters');
	}
	return trimmed;
}

export function normalizeNotePriority(value: string | null | undefined): NotePriority {
	if (value === null || value === undefined || value === '') return 'normal';
	if ((NOTE_PRIORITIES as readonly string[]).includes(value)) {
		return value as NotePriority;
	}
	throw new Error('priority must be one of normal, high, urgent');
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
		display_name: normalizeNoteDisplayName(input.display_name),
		priority: normalizeNotePriority(input.priority),
		ssh_password: input.ssh_password.trim(),
		content: input.content.trim(),
		expires_at: input.expires_at,
		kind,
		gpu_indices: gpuIndices
	};
}
