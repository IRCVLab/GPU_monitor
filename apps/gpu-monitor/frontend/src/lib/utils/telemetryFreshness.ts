export function isTelemetryStale(lastSeen: string | null, nowMs: number, maxAgeMs: number): boolean {
	if (!lastSeen) return true;
	const lastSeenMs = Date.parse(lastSeen);
	if (Number.isNaN(lastSeenMs)) return true;
	return nowMs - lastSeenMs >= maxAgeMs;
}
