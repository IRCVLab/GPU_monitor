"""Add the collector's judgments to a projected v1 snapshot without changing stored data."""
from collector import judge
from collector.inventory import CapacityThresholds


def snapshot_response(snapshot, thresholds=CapacityThresholds()):
    response = dict(snapshot)
    if "mounts" in snapshot:
        response["mounts"] = [
            {**mount, **judge.capacity_fields(mount, thresholds)} for mount in snapshot["mounts"]
        ]
    for table in ("top_files", "stale"):
        if table in snapshot:
            response[table] = [
                {**row, "age_days": judge.age_days(row.get("mtime"), snapshot.get("scan_started_unix"))}
                for row in snapshot[table]
            ]
    return response
