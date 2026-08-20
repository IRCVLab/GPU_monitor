import type { Note, NotePriority } from '../types.ts';

export interface NotePriorityMeta {
	label: string;
	className: string;
}

export interface HoldAdvisory {
	primary: Note | null;
	secondaryCount: number;
	secondarySummary: string;
	ordered: Note[];
}

const NOTE_PRIORITY_META: Record<NotePriority, NotePriorityMeta> = {
	normal: { label: '보통', className: 'note-priority--normal' },
	high: { label: '높음', className: 'note-priority--high' },
	urgent: { label: '개초비상', className: 'note-priority--urgent' }
};

const NOTE_PRIORITY_RANK: Record<NotePriority, number> = {
	urgent: 0,
	high: 1,
	normal: 2
};

function priorityRank(priority: NotePriority): number {
	return NOTE_PRIORITY_RANK[priority];
}

function createdRank(createdAt: string): number {
	const parsed = Date.parse(createdAt);
	return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

export function resolveDisplayName(note: Pick<Note, 'display_name' | 'username'>): string {
	const trimmed = note.display_name?.trim();
	return trimmed ? trimmed : note.username;
}

export function getNotePriorityMeta(priority: NotePriority): NotePriorityMeta {
	return NOTE_PRIORITY_META[priority];
}

export function rankHoldNotes(notes: readonly Note[]): Note[] {
	return notes
		.map((note, index) => ({ note, index }))
		.filter(({ note }) => note.kind === 'hold')
		.sort((a, b) => {
			return (
				priorityRank(a.note.priority) - priorityRank(b.note.priority) ||
				createdRank(a.note.created_at) - createdRank(b.note.created_at) ||
				a.index - b.index
			);
		})
		.map(({ note }) => note);
}

export function formatAdditionalHoldSummary(count: number): string {
	return count > 0 ? `+${count}` : '';
}

export function buildHoldAdvisory(notes: readonly Note[]): HoldAdvisory {
	const ordered = rankHoldNotes(notes);
	const secondaryCount = Math.max(0, ordered.length - 1);
	return {
		primary: ordered[0] ?? null,
		secondaryCount,
		secondarySummary: formatAdditionalHoldSummary(secondaryCount),
		ordered
	};
}
