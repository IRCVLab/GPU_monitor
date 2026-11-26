"""Pure judgments shared by the storage monitor's v1 response projections."""
from math import floor, isfinite
from typing import Any, Mapping, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from collector.inventory import CapacityThresholds


WARNING_USED_PCT = 80
CRITICAL_USED_PCT = 92
WARNING_FREE_BYTES = 512 * 1024**3
CRITICAL_FREE_BYTES = 128 * 1024**3
MAX_SAFE_INTEGER = 2**53 - 1
LOCAL_SCAN_CADENCE_SECONDS = 6 * 3600
FRESH_SNAPSHOT_SECONDS = LOCAL_SCAN_CADENCE_SECONDS + 3600
ROOT_SCAN_CADENCE_SECONDS = {"ssd": 3600, "hdd": 10800, "mixed": 10800}
SCHEDULED_TIMER_DELAY_SECONDS = 360
ACTIVE_TELEMETRY_INTERVAL_SECONDS = 5
IDLE_TELEMETRY_INTERVAL_SECONDS = 60
MAX_CAPACITY_AGE_SECONDS = 900
# Design §3.2: two missed idle observes plus the timer's randomized delay.
VOLUME_GRACE_SECONDS = 2 * IDLE_TELEMETRY_INTERVAL_SECONDS + SCHEDULED_TIMER_DELAY_SECONDS
# The v1 freshness grace, sized for the old 15-minute status poll. Kept so the v1 badge does not move.
FRESHNESS_SLACK_SECONDS = 900


def host_now(host_unix: Optional[int], received_monotonic: Optional[float], now_monotonic: float) -> Optional[int]:
    """Advance a host clock sample by locally measured elapsed time."""
    if host_unix is None or received_monotonic is None:
        return None
    return int(host_unix + max(0, now_monotonic - received_monotonic))


def freshness(payload: Optional[Mapping[str, Any]], now: Optional[int], polling_interval: int) -> str:
    finished = payload.get("scan_finished_unix") if payload else None
    if type(finished) is not int or now is None:
        return "unknown"
    roots = payload.get("selected_roots", ())
    if roots and all(type(root.get("cadence_sec")) is int for root in roots):
        # v2 volumes carry the agent's own schedule (S1). The worst volume decides; capacity never renews a tree.
        return "stale" if any(now - root["scan_finished_unix"] > root["cadence_sec"] + VOLUME_GRACE_SECONDS
                              for root in roots) else "fresh"
    deadline = finished + FRESH_SNAPSHOT_SECONDS
    for root in payload.get("selected_roots", ()):
        root_finished = root.get("scan_finished_unix")
        if root_finished is not None:
            cadence = ROOT_SCAN_CADENCE_SECONDS.get(root.get("storage_media"), 10800)
            deadline = min(deadline, root_finished + cadence + polling_interval + SCHEDULED_TIMER_DELAY_SECONDS)
    return "stale" if now > deadline else "fresh"


FRESHNESS_ORDER = ("fresh", "unknown", "stale")


def worst_freshness(*states: str) -> str:
    return max(states, key=FRESHNESS_ORDER.index)


def _valid_bytes(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def used_pct(mount: Mapping[str, Any]) -> Optional[int]:
    """Use the reported df percentage, falling back to the v1 overview byte ratio."""
    total, used = mount.get("df_total"), mount.get("df_used")
    if not _valid_bytes(total) or not _valid_bytes(used):
        return None
    pct = mount.get("df_use_pct")
    if type(pct) not in (float, int) or not isfinite(pct):
        if not total:
            return None
        pct = (used / total) * 100
    return min(100, max(0, floor(pct + 0.5)))


def severity(pct: Optional[int], free: Optional[int], thresholds: "CapacityThresholds") -> str:
    if pct is None or free is None:
        return "unknown"
    if pct >= thresholds.critical_used_pct or free <= thresholds.critical_free_bytes:
        return "critical"
    if pct >= thresholds.warning_used_pct or free <= thresholds.warning_free_bytes:
        return "warning"
    return "normal"


def capacity_fields(mount: Mapping[str, Any], thresholds: "CapacityThresholds") -> Mapping[str, Any]:
    pct = used_pct(mount)
    free = mount.get("df_avail")
    return {"used_pct": pct, "severity": severity(pct, free if _valid_bytes(free) else None, thresholds)}


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


def volume_freshness(finished, cadence, now):
    if finished is None or now is None:
        return "unknown"
    return "stale" if now > finished + cadence + VOLUME_GRACE_SECONDS else "fresh"


def volume_capacity(sample, now, thresholds):
    if sample is None:
        return None, "unknown"
    current = sample.status == "ok" and now is not None and capacity_sample_current(sample.observed_unix, now)
    usable = sample.used + sample.avail
    pct = -(-sample.used * 100 // usable) if current and usable else None
    return {"total": sample.total if current else None, "used": sample.used if current else None,
            "avail": sample.avail if current else None, "used_pct": pct, "observed_unix": sample.observed_unix,
            "source": "live", "status": sample.status}, severity(pct, sample.avail, thresholds)
