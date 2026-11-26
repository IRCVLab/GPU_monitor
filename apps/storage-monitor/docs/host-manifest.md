# Host manifest and central inventory

There are two host lists with different jobs:

- `tests/fixtures/fleet/hosts.json` is a tracked fixture fleet manifest for sample files.
- `/etc/storage-viz/servers.json` is the central production inventory consumed by `storage-viz-dashboard.service`.

## Fixture fleet manifest

`tests/fixtures/fleet/hosts.json` maps sample snapshots for local development:

```json
[
  { "id": "hinton", "label": "hinton", "file": "hinton", "default": true, "sample_data": true }
]
```

Each entry maps to `<file>.json` in the fixture directory. The viewer requests these snapshots through the v1 API.

## Central production inventory

The central dashboard API (`/api/servers`) is backed by `/etc/storage-viz/servers.json`. Each enabled server entry includes display metadata, strict SSH coordinates, and scanner configuration digest material. Identity and host-key files are paths outside the repository, for example `/etc/storage-viz/keys/<server>_ed25519` and `/etc/storage-viz/known_hosts`.

Required production fields are documented in `config/servers.example.yaml`. Do not add password, token, inline private key, shell command, arbitrary SSH argument, or scan-root fields to the inventory.
