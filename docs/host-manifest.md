# Host manifest

The viewer chooses which JSON file to load from the tracked host manifest at
`data/hosts.json`:

```json
[
  { "id": "hinton", "label": "hinton", "file": "hinton", "default": true }
]
```

Each entry maps to `data/<file>.json`, with `data/<file>.sample.json` as a
fallback for demos. With `viewer/serve.py`, `/data/...` is served from
`STORAGE_VIZ_DATA_DIR`, so host JSON files can live outside the viewer directory.
Plain static serving still works through the checked-in `viewer/data -> ../data`
symlink.

Recommended host entry fields:

| Field | Required | Description |
| --- | --- | --- |
| `id` | yes | Stable DOM/select value. Use lowercase hostname-style text. |
| `label` | yes | Human-readable label shown in the dropdown. |
| `file` | yes | JSON basename without `.json`. |
| `default` | no | If true, this host is shown first. |

For a new host named `lecun`:

1. Run or copy a scan to `$STORAGE_VIZ_DATA_DIR/lecun.json`.
2. Add `{ "id": "lecun", "label": "lecun", "file": "lecun" }` to `data/hosts.json`.
3. Reload the dashboard and select the host.
