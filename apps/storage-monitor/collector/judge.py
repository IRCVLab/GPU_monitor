"""Pure judgments shared by the storage monitor's volume and server views."""
from dataclasses import dataclass
from collector.contract import Capacity
from typing import Any, Mapping, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from collector.inventory import CapacityThresholds


WARNING_USED_PCT = 80
CRITICAL_USED_PCT = 92
WARNING_FREE_BYTES = 512 * 1024**3
CRITICAL_FREE_BYTES = 128 * 1024**3
LOCAL_SCAN_CADENCE_SECONDS = 6 * 3600
SCHEDULED_TIMER_DELAY_SECONDS = 360
ACTIVE_TELEMETRY_INTERVAL_SECONDS = 5
IDLE_TELEMETRY_INTERVAL_SECONDS = 60
# Design §3.2: two missed idle observes plus the timer's randomized delay.
VOLUME_GRACE_SECONDS = 2 * IDLE_TELEMETRY_INTERVAL_SECONDS + SCHEDULED_TIMER_DELAY_SECONDS
# The agent samples every volume's capacity on each run of its hourly timer
# (storage-viz-scan.timer, nothing-due runs included), so a sample is current for one
# timer period plus the same grace as volume freshness.
AGENT_TIMER_INTERVAL_SECONDS = 3600
MAX_CAPACITY_AGE_SECONDS = AGENT_TIMER_INTERVAL_SECONDS + VOLUME_GRACE_SECONDS


def host_now(host_unix: Optional[int], received_monotonic: Optional[float], now_monotonic: float) -> Optional[int]:
    """Advance a host clock sample by locally measured elapsed time."""
    if host_unix is None or received_monotonic is None:
        return None
    return int(host_unix + max(0, now_monotonic - received_monotonic))


FRESHNESS_ORDER = ("fresh", "unknown", "stale")


def worst_freshness(*states: str) -> str:
    return max(states, key=FRESHNESS_ORDER.index)


def severity(pct: Optional[int], free: Optional[int], thresholds: "CapacityThresholds") -> str:
    if pct is None or free is None:
        return "unknown"
    if pct >= thresholds.critical_used_pct or free <= thresholds.critical_free_bytes:
        return "critical"
    if pct >= thresholds.warning_used_pct or free <= thresholds.warning_free_bytes:
        return "warning"
    return "normal"


def age_days(mtime: Optional[int], scan_started_unix: Optional[int]) -> Optional[int]:
    if type(mtime) is not int or type(scan_started_unix) is not int:
        return None
    return max(0, (scan_started_unix - mtime) // 86400)


def eta(history: Mapping[str, Sequence[Sequence[int]]], plan: str, agent_estimate: Any) -> Tuple[Optional[int], Optional[int]]:
    """Typical (lower median) and longest duration among recent completions of one plan."""
    candidates = [duration for _, duration in history.get(plan, [])]
    if type(agent_estimate) is int and agent_estimate >= 0:
        candidates.append(agent_estimate)
    if not candidates:
        return None, None
    ordered = sorted(candidates)
    return ordered[(len(ordered) - 1) // 2], ordered[-1]


def unit_is_active(unit_state: str) -> bool:
    return unit_state in {"active", "activating", "reloading"}


def activity(status: Mapping[str, Any], received_monotonic: Optional[float], now_monotonic: float) -> str:
    """Expire a host observation after two missed telemetry intervals."""
    state = status.get("state")
    interval = ACTIVE_TELEMETRY_INTERVAL_SECONDS if state == "running" else IDLE_TELEMETRY_INTERVAL_SECONDS
    if received_monotonic is None or now_monotonic - received_monotonic > 2 * interval:
        return "unknown"
    if status.get("error_code") == "SCAN_INTERRUPTED":
        return "interrupted"
    if state == "running":
        return "running"
    if state in {"idle", "complete", "partial", "failed"}:
        return "idle"
    return "unknown"


def capacity_sample_current(observed_unix: int, now: int) -> bool:
    return 0 <= now - observed_unix <= MAX_CAPACITY_AGE_SECONDS


def progress_stale(updated_unix: Optional[int], now: int) -> bool:
    return updated_unix is not None and now - updated_unix > 30


def volume_freshness(finished, cadence, now, scanning=False):
    """A volume's data is fresh until its next scan is overdue.

    The agent checks due groups only on its hourly timer, so a volume that comes due
    just after a tick waits up to one more timer period before its scan starts. A
    volume whose group is being scanned right now is not stale: a large HDD scan can
    run for hours past its cadence.
    """
    if finished is None or now is None:
        return "unknown"
    if scanning:
        return "fresh"
    return "stale" if now > finished + cadence + AGENT_TIMER_INTERVAL_SECONDS + VOLUME_GRACE_SECONDS else "fresh"


@dataclass(frozen=True)
class ScanCapacity(Capacity):
    used_pct: int


def volume_capacity(sample, now, thresholds):
    if sample is None:
        return None, "unknown"
    if isinstance(sample, ScanCapacity):
        return {"total": sample.total, "used": sample.used, "avail": sample.avail, "used_pct": sample.used_pct,
                "observed_unix": sample.observed_unix, "source": "scan", "status": sample.status}, severity(
                    sample.used_pct, sample.avail, thresholds)
    current = sample.status == "ok" and now is not None and capacity_sample_current(sample.observed_unix, now)
    usable = sample.used + sample.avail
    pct = -(-sample.used * 100 // usable) if current and usable else None
    return {"total": sample.total if current else None, "used": sample.used if current else None,
            "avail": sample.avail if current else None, "used_pct": pct, "observed_unix": sample.observed_unix,
            "source": "live", "status": sample.status}, severity(pct, sample.avail, thresholds)
