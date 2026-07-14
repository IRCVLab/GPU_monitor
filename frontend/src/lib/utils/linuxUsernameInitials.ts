export interface LinuxUsernameInitials {
	initials: string;
	seed: number;
}

const TOKEN_PATTERN = /[\p{L}\p{N}]+/gu;

function hashUsername(value: string): number {
	let hash = 2166136261;
	for (const character of value) {
		hash ^= character.codePointAt(0) ?? 0;
		hash = Math.imul(hash, 16777619);
	}
	return hash >>> 0;
}

function firstCharacters(value: string, count: number): string {
	return Array.from(value.toUpperCase()).slice(0, count).join('');
}

export function getLinuxUsernameInitials(username: string): LinuxUsernameInitials {
	const normalized = username.trim();
	if (!normalized) {
		return {
			initials: '?',
			seed: 0
		};
	}

	const tokens = normalized.match(TOKEN_PATTERN) ?? [];
	const [firstToken = '', secondToken = ''] = tokens;
	const initials =
		tokens.length >= 2
			? `${firstCharacters(firstToken, 1)}${firstCharacters(secondToken, 1)}`
			: tokens.length === 1
				? firstCharacters(firstToken, 2)
				: firstCharacters(normalized.replace(/\s+/g, ''), 2) || '?';

	return {
		initials: initials || '?',
		seed: hashUsername(normalized.toLowerCase())
	};
}
