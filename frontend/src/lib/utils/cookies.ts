const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365;

export function readCookie(name: string): string | null {
	if (typeof document === 'undefined') return null;

	const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${escaped}=([^;]+)`));
	if (!match) return null;

	try {
		return decodeURIComponent(match[1]);
	} catch {
		return match[1];
	}
}

export function writeCookie(name: string, value: string, maxAge = ONE_YEAR_SECONDS): void {
	if (typeof document === 'undefined') return;

	document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${maxAge}; SameSite=Lax`;
}
