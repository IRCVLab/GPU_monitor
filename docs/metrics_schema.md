# Metrics API Schema

The monitoring API returns a `ServerSnapshot` for each alias when `/stats` is
requested. The structure is designed to make it easy to plug in additional
collectors (CPU, storage, network) without changing the client code.

```jsonc
{
  "alias": "00Poseidon",
  "last_updated": "2025-11-13T12:18:35+09:00",
  "resources": {
    "gpu": {
      "summary": {
        "count": 4,
        "total_memory": 97280,
        "total_memory_used": 53120,
        "avg_utilization": 57.2
      },
      "gpus": [
        {
          "id": 0,
          "name": "NVIDIA RTX 6000",
          "memory": { "used": 20480, "total": 24576, "percent": 83.3 },
          "utilization": { "gpu": 75.0, "memory": 62.0 },
          "temperature": 68.0,
          "power": 210.5,
          "processes": [
            {
              "pid": 12345,
              "username": "alice",
              "command": "python train.py",
              "memory_used": 10240
            }
          ]
        }
      ],
      "raw": { "...": "original gpustat payload (optional)" }
    },
    "cpu": {
      "summary": {
        "cpu_percent": 63.4,
        "load_1": 1.12,
        "load_5": 0.98,
        "load_15": 0.87,
        "core_count": 32
      },
      "cores": [
        { "id": 0, "percent": 55.1 },
        { "id": 1, "percent": 64.3 }
      ]
    },
    "storage": {
      "summary": {
        "mount_count": 4,
        "total": 1099511627776,
        "used": 769658139648,
        "percent": 70.0
      },
      "mounts": [
        {
          "mount": "/",
          "device": "/dev/nvme0n1p2",
          "fs_type": "ext4",
          "size": 512000000000,
          "used": 341333000000,
          "available": 170667000000,
          "percent": 66.6
        }
      ]
    }
  },
  "metadata": {
    "host": "166.104.167.11",
    "port": 2201
  },
  "status": "online" // or "offline" when collectors fail
}
```

Notes:

- Each collector is responsible for filling the `resources.<name>` object.
- If a collector fails, an `error` string is attached instead of the payload.
- `metadata` is extensible for future attributes (rack, owner, etc.).
- Clients should inspect `status` and per-resource `error` fields before using
  the metrics.
