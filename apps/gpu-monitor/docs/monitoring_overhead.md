# Monitoring Overhead

| Collector | Command / Action | Default Interval | Expected Load |
|-----------|------------------|------------------|---------------|
| GPU       | `gpustat --json` (nvml) | ~5s (per `ServerMonitor` loop) | NVML read; negligible CPU, <50 ms |
| CPU/Mem   | Python snippet reading `/proc/stat` twice (200 ms delta) + `/proc/meminfo` | ~5s | Reads kernel counters; negligible |
| Storage   | Python snippet parsing `/proc/mounts` + `os.statvfs` per local mount | **cached for 60s** (collector cache) | Light filesystem metadata reads once per minute |

Notes:
- `ServerMonitor` fetches metrics 5s after a successful cycle; failures back off to ~15s. SSH session stays open, so only the collector commands execute remotely.
- Storage stats are cached inside `StorageCollector` (`monitoring_core/collectors/storage.py`) so even though the monitor loop runs every 5s, the storage script executes at most once per minute per server.
- Each metric push performs only reads (`gpustat`, `/proc/*`, `statvfs`). No processes are spawned on the remote host other than the three short-lived commands above.
- To further reduce load you can increase `cache_ttl_seconds` when registering the storage collector or run additional collectors on longer cadences.
