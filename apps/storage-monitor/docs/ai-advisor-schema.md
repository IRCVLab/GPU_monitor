# AI Advisor recommendation schema v1

The AI Cleanup Advisor returns a separate recommendation payload from the
scanner snapshot schema. The scanner continues to emit `docs/schema-v1.md`; the
advisor reads that snapshot, builds bounded evidence, and returns suggestions for
humans to review.

## Safety invariants

- Recommendations are suggestions only. The advisor never executes `rm`, `mv`,
  `sudo`, `chmod`, `chown`, or any other filesystem mutation.
- Browser actions may copy or stage human-reviewed cleanup commands only for
  delete-safe recommendations. Move, dedupe, archive, investigate, and keep
  recommendations open details; they must not silently become `rm -rf` commands.
- The LLM receives compact evidence prepared by storage-viz. It never receives
  shell access, direct filesystem handles, or arbitrary browser-supplied paths.
- Invalid, top-level, system, mount-root, relative, or null-byte paths must be
  rejected before a recommendation reaches the UI.
- Unknown additive fields may be ignored, but missing required fields or unknown
  enum values make a recommendation invalid.

## Top-level payload

```json
{
  "schema_version": 1,
  "host_id": "hinton",
  "snapshot_fingerprint": "sha256-or-stable-fingerprint",
  "generated_at_unix": 1770000000,
  "mode": "rule-only",
  "summary": {
    "health": "warning",
    "headline": "Checkpoints and caches dominate the pressured SSD mount",
    "top_drivers": ["/data1 is 98% full", "large checkpoint directories"]
  },
  "recommendations": []
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | number | yes | Current advisor schema version, `1`. |
| `host_id` | string | yes | Host manifest id used to resolve the snapshot. |
| `snapshot_fingerprint` | string | yes | Stable fingerprint of the analyzed snapshot and analyzer inputs. |
| `generated_at_unix` | number | yes | Unix timestamp for advisor generation. |
| `mode` | enum | yes | `rule-only`, `rule+llm`, or `mock`. |
| `summary` | object | yes | Health headline and major storage drivers. |
| `recommendations` | array | yes | Validated recommendation objects. |

### Summary object

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `health` | enum | yes | `critical`, `warning`, or `ok`. |
| `headline` | string | yes | Short operator-facing summary. |
| `top_drivers` | string[] | yes | Evidence-backed drivers such as full mounts or large artifact classes. |

## Recommendation object

```json
{
  "id": "stable-recommendation-id",
  "action": "delete",
  "category": "pip-cache",
  "target_path": "/home/alice/.cache/pip",
  "related_paths": [],
  "mount": "/home",
  "owner": "alice",
  "bytes": 123456789,
  "priority": "high",
  "confidence": 0.92,
  "risk": "low",
  "badge": "AI: cache cleanup",
  "reason_short": "pip cache is unusually large",
  "reason_detail": "The path matches .cache/pip and the snapshot reports a large rebuildable cache on a pressured mount.",
  "evidence": [
    { "type": "size", "label": "candidate size", "value": 123456789 },
    { "type": "path_pattern", "label": "matched .cache/pip", "value": true }
  ],
  "suggested_next_step": "review-delete-command"
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Stable id derived from host, snapshot, action, category, and target path. |
| `action` | enum | yes | `delete`, `move`, `dedupe`, `archive`, `investigate`, or `keep`. |
| `category` | enum | yes | See category list below. |
| `target_path` | string | yes | Absolute path or snapshot path being discussed. Must pass safety filters. |
| `related_paths` | string[] | no | Optional additional safe paths for dedupe/move context. |
| `mount` | string | no | Mount path from the snapshot, when known. |
| `owner` | string | no | File owner from snapshot rows, when known. |
| `bytes` | number | no | Candidate byte count from snapshot/evidence. |
| `priority` | enum | yes | `critical`, `high`, `medium`, or `low`. |
| `confidence` | number | yes | `0.0` through `1.0`; low confidence should prefer `investigate`. |
| `risk` | enum | yes | `low`, `medium`, or `high`. |
| `badge` | string | yes | Compact UI label for treemap/table badges. |
| `reason_short` | string | yes | One-line explanation. |
| `reason_detail` | string | yes | Evidence-grounded detail with no hallucinated facts. |
| `evidence` | array | yes | Typed evidence items from snapshot or read-only metadata. |
| `suggested_next_step` | enum | yes | See next-step list below. |

### Category enum

- `pip-cache`
- `env`
- `log`
- `checkpoint`
- `duplicate`
- `ssd-misplacement`
- `stale-large`
- `blocked-scan`
- `other`

### Suggested next step enum

- `review-delete-command` — only valid for low-risk `delete` recommendations
  that can be added to the existing cleanup command panel for human review.
- `move-to-hdd` — explain a move/archive strategy; never auto-generate delete
  commands.
- `compare-duplicates` — ask the user to compare related paths before removing
  any copy.
- `inspect-owner` — route to owner/project review before action.
- `keep` — explicitly preserve data or document why deletion is unsafe.

## Exclusion request model

The browser stores exclusions in localStorage and sends them with future advisor
requests. Server-side filtering applies the same exclusions again for defense in
depth.

```json
{
  "version": 1,
  "host_id": "hinton",
  "items": [
    { "type": "recommendation", "id": "...", "created_at": 1770000000 },
    { "type": "path", "path": "/data/project/do-not-touch", "created_at": 1770000000 },
    { "type": "pattern", "pattern": "/home/*/active-project/**", "created_at": 1770000000 },
    { "type": "action", "action": "move", "created_at": 1770000000 }
  ]
}
```

## Validation rules

1. Reject malformed JSON, missing required fields, and unknown enum values.
2. Reject paths that are not absolute, contain NUL bytes, are `/`, are one
   segment such as `/home`, are system-critical roots, or are mount roots unless
   an explicitly safe future policy says otherwise.
3. Reject recommendations with delete-like next steps unless `action=delete`,
   `risk=low`, and evidence supports a rebuildable/stale candidate.
4. Prefer `investigate` when evidence is weak, duplicate proof is heuristic-only,
   or blocked scan data means the snapshot may undercount important paths.
5. Preserve rule-only recommendations when LLM output is invalid; surface the LLM
   failure as advisor status instead of hiding deterministic findings.
