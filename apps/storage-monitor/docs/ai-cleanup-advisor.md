# AI Cleanup Advisor

The AI Cleanup Advisor is an optional, local-first assistant for understanding
why storage is unhealthy and which cleanup actions are worth reviewing. It is not
a deletion bot. It turns the existing scanner snapshot into badges, details, and
human-reviewed next steps.

## What it recommends

The advisor is action-taxonomy based rather than delete-only:

- `delete`: rebuildable or stale low-risk candidates such as package caches.
- `move`: logs, checkpoints, or datasets that should leave an expensive/pressure
  SSD tier for cheaper HDD/archive storage.
- `dedupe`: likely duplicate artifacts that need comparison before removal.
- `archive`: cold large artifacts that may belong in long-term storage.
- `investigate`: high-risk or weak-evidence candidates that need owner/project
  review.
- `keep`: explicit do-not-delete guidance when evidence indicates active or risky
  data.

Every badge should open a details view with the target path, action, category,
risk, confidence, evidence, and suggested next step.

## Execution and authority model

The advisor cannot execute filesystem mutations. The safe path is:

```text
schema-v1 snapshot
  -> deterministic analyzer builds compact evidence
  -> optional read-only metadata collector adds bounded metadata
  -> optional local LLM pass 1 analyzes/prioritizes that evidence in English
  -> optional local LLM pass 2 translates the validated user-facing output to Korean
  -> schema validator and safety filters drop invalid advice
  -> viewer displays badges/details/exclusions
  -> humans copy/review commands when appropriate
```

The LLM never gets direct filesystem, shell, sudo, delete, move, chmod/chown, or
file-content authority. If live evidence is enabled, storage-viz owns that
inspection and returns sanitized metadata only.

## Runtime modes

| Mode | When used | Behavior |
| --- | --- | --- |
| Disabled | default | `/ai/status` reports disabled; dashboard still works. |
| `rule-only` | AI endpoint enabled without LLM provider, or LLM unavailable | Deterministic recommendations from snapshot evidence. |
| `mock` | tests/demos | Stable fixture output without a live model. |
| `rule+llm` | explicit local model config | Rule evidence plus local LLM synthesis, validated before display. |

Tests and CI must pass in `rule-only` or `mock` mode. A live model server is not
required for correctness checks.

`mock` is for tests and UI demos only. Operator-facing deployments should use
`ollama`, `openai-compatible`, or rule-only fallback so the UI does not pretend a
real model ran when no model server is available.

## Prompting contract

The LLM path is intentionally two-pass:

1. **Analyzer pass**: Think and write structured JSON in English. It must use
   only the evidence pack, avoid delete-heavy guesses, and prefer actions such
   as move/archive/dedupe/investigate when the evidence is not a rebuildable
   cache.
2. **Translator pass**: Translate the validated analyzer JSON into Korean
   user-facing fields while preserving ids, paths, actions, risk, confidence,
   evidence, and suggested next steps.

The final dashboard output is Korean by default (`STORAGE_VIZ_AI_OUTPUT_LANGUAGE=ko`).
Clients also send `language=ko` with `/ai/recommend`, and the server applies that
request-level language before building the response.

## Local model default

Use runtime configuration rather than hardcoding a provider. The project default
recommendation for GPU servers is:

```bash
STORAGE_VIZ_AI_ENABLED=1
STORAGE_VIZ_AI_PROVIDER=ollama
STORAGE_VIZ_AI_ENDPOINT=http://127.0.0.1:11434
STORAGE_VIZ_AI_MODEL=qwen2.5:14b
```

`qwen2.5:14b` is the documented default because cleanup advising is a structured
evidence-to-JSON/explanation task: it gives solid cleanup judgment and bilingual
explanations while staying much easier to serve than 27B/70B-class models.
Operators can override it for latency or quality:

- Fast fallback: `qwen2.5:7b` or another 7B-9B instruct model.
- Default GPU advisor: `qwen2.5:14b`.
- High-quality batch mode: `qwen2.5:32b` or `llama3.3:70b` when VRAM/latency
  budgets allow.

The runtime deliberately asks the LLM for compact patches instead of a full
recommendation schema.  The server then merges those patches back into validated
rule evidence, so path/byte/evidence fields stay deterministic and the two-pass
analysis/translation path avoids long full-schema generations.

## Optional read-only inspection

Snapshot data is enough for the first advisor pass. Deeper live evidence must be
separately enabled:

```bash
STORAGE_VIZ_AI_READONLY_INSPECTION=1
STORAGE_VIZ_AI_ALLOWED_ROOTS=/home,/data,/data1,/data3
STORAGE_VIZ_AI_MAX_INSPECT_PATHS=25
STORAGE_VIZ_AI_MAX_INSPECT_DEPTH=2
STORAGE_VIZ_AI_INSPECT_TIMEOUT_SEC=5
```

Inspection rules:

- Disabled by default and independent from `STORAGE_VIZ_AI_ENABLED`.
- Allowlisted roots only; reject `/`, top-level/system paths, relative paths,
  NUL-byte paths, and symlink escapes.
- Metadata-only by default: `stat`, shallow entry counts, mtime ranges, extension
  summaries, and bounded aggregate sizes.
- No file contents, no shell strings, no sudo, no writes, and no unbounded symlink
  traversal.
- Enforce max paths, depth, entry count, timeout, and returned evidence size.

## Viewer behavior

- Existing dashboard rendering must not wait for AI.
- Static/offline viewer mode should show a disabled/non-blocking AI state.
- AI analysis is launched from the global header control, not only from the AI
  tab. The AI tab remains for compact grouped summaries, details, and exclusions.
- Badges annotate treemap tiles and Top/Stale rows but must not cover core size
  labels or cleanup-selection markers.
- Badge clicks open details. They do not toggle cleanup selection directly.
- Only safe delete recommendations with `suggested_next_step=review-delete-command`
  may call the existing cleanup command panel.
- Move, dedupe, archive, investigate, and keep recommendations remain details-only
  unless future explicitly reviewed workflows are added.

## Exclusions

Initial exclusion persistence is browser-local localStorage so static deployments
and no-auth servers remain simple. Users can exclude:

- one recommendation id;
- one path;
- a path pattern;
- an action type such as `move`;
- a category such as `checkpoint`.

The browser includes exclusions in `/ai/recommend` requests, and the server
filters again before returning results.

## Privacy and operations notes

Production snapshots can expose private paths, user names, project names, and
activity patterns. Keep AI disabled by default and prefer local providers. Do not
send raw snapshots or live filesystem metadata to remote services unless a site
operator has explicitly reviewed privacy and policy implications.

See `docs/ai-advisor-schema.md` for the JSON contract and `docs/operations.md`
for environment variables and serving examples.
