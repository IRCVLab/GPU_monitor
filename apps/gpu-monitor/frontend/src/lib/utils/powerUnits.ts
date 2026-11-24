/** Telemetry remains in watts; every power control and label uses kilowatts. */
export function kilowatts(watts: number | null | undefined): number | null {
 return watts == null || !Number.isFinite(watts) ? null : watts / 1000;
}
export function kilowattsLabel(watts: number | null | undefined): string {
 const value = kilowatts(watts);
 return `${value === null ? '—' : value.toLocaleString('ko-KR', {maximumFractionDigits: 3})} kW`;
}
export function wattsFromKilowatts(value: number | null): number | null {
 return value === null || !Number.isFinite(value) ? null : Math.round(value * 1000000) / 1000;
}
