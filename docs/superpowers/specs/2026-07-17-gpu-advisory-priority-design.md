# GPU Advisory Priority Design

## Status
- Status: Draft for implementation planning.
- Date: 2026-07-17.
- Scope: DEV repo only, Full and Compact note surfaces.
- Live invariant: `/home/ircv/workspace/monitoring_v2` remains untouched.
- Change-control invariant: implementation may edit code, tests, and docs in the DEV repo as listed by the implementation plan; this design document does not authorize commits or LIVE edits.

## Problem
The current Note hold UX is advisory but flat: a hold has no priority, GPU cues only show the author username, and old records rely on implicit defaults. That makes multiple holds on the same GPU ambiguous, hides the most actionable hold, and leaves the UI copy too generic for dense operator workflows.

The feature must add priority without changing GPU availability truth, keep memo behavior unchanged, and remain backward-compatible with older rows and API payloads.

## Data model and fallback rules
- Keep `kind` as `memo | hold`; memo stays the default.
- Add `priority` as `normal | high | urgent`; default `normal`.
- Add `display_name` as nullable text with a max length of 40 after trimming.
- Database/API storage and responses keep `display_name` as nullable raw display text:
  - omitted, `null`, empty, and whitespace-only values normalize to `null`;
  - non-empty values are trimmed and serialized as that trimmed value;
  - the API does not replace missing `display_name` with `username`.
- UI display fallback is centralized in `resolveDisplayName(note)`: use `display_name` when present, otherwise use `username`.
- `display_name` is display-only; authorization, ownership checks, and delete behavior continue to use `username`.
- Old rows or older API payloads that omit `priority` normalize to `normal`.
- Old rows or older API payloads that omit `display_name`, send `null`, or send whitespace normalize to `display_name: null`; only UI rendering falls back to `username`.
- The backend must accept and serialize those fields without breaking existing memo rows.

## UI decisions
- Memo creation stays unchanged when no GPU is selected.
- Selecting one or more GPUs reveals the compact hold inputs: priority and optional display name.
- Hold copy is action-centered and concise; avoid reserved/exclusive wording.
- `normal` is neutral, `high` and `urgent` are text-visible and use semantic warning/destructive color.
- GPU row cue text uses `resolveDisplayName(note)` so display-name fallback is consistent and UI-only.
- If multiple holds target the same GPU, choose one deterministic primary cue by priority first, then soonest expiry, then a stable tie-breaker such as created time or note id.
- Hold ranking is a frontend advisory display concern only and belongs in the shared `noteAdvisory` helper. The backend must not add ranking behavior or ranking-specific tests.
- The remaining matching holds are summarized as `+N`.
- Full `GpuBar` may not rely on title-only hints. Hover and keyboard focus must reveal the same custom tooltip with `role="tooltip"`.
- Full `GpuBar` tooltip content must include GPU index/name, resolved display name, priority, expiry, and memo text/summary.
- Full `GpuBar` tooltip closes on pointer leave, focus blur, and `Escape`.
- Availability state stays derived only from telemetry; advisory holds never change available/occupied classification.
- The Full card uses an exact `0.4rem` gap between the card header and the GPU list.

## Accessibility
- Every priority label must remain text-visible; color alone must not carry meaning.
- Tooltip content must be reachable by keyboard focus and dismissible without a mouse-only gesture.
- Hover and focus expose the same tooltip content and `Escape` dismisses it.
- Compact and Full cues must keep the GPU index/name, resolved display name, priority, expiry, and memo context readable to assistive tech.
- Reduced-motion users keep the same semantics without animation dependency.

## Implementation impact
- Backend: `backend/models.py`, `backend/database.py`, `backend/routers/notes.py`, `backend/tests/test_notes_validation.py`, `backend/tests/test_note_priority.py`.
- Frontend data: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/api.contract.test.ts`, `frontend/src/lib/utils/notePayload.ts`, `frontend/src/lib/utils/notePayload.test.ts`, `frontend/src/lib/utils/noteAdvisory.ts`, `frontend/src/lib/utils/noteAdvisory.test.ts`.
- Full view: `frontend/src/lib/components/NoteForm.svelte`, `frontend/src/lib/components/ServerCard.svelte`, `frontend/src/lib/components/GpuBar.svelte`, `frontend/src/lib/components/NoteForm.contract.test.ts`, `frontend/src/lib/components/ServerCard.note-contract.test.ts`, `frontend/src/lib/components/GpuBar.contract.test.ts`, `frontend/src/lib/styles/monitor-cards.css`.
- Compact view: `frontend/src/lib/components/CompactDashboard.svelte`, `frontend/src/lib/components/CompactServerRow.svelte`, `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`, `frontend/src/routes/page-view.contract.test.ts`, `frontend/src/lib/styles/monitor-compact.css`.
- Browser QA: existing Playwright CLI wrapper `/Users/shchoi/.codex/skills/playwright/scripts/playwright_cli.sh` against current DEV `http://127.0.0.1:5174`.

## Acceptance criteria
1. `display_name` is nullable, capped at 40 trimmed characters, and normalizes omitted/null/empty/whitespace values to `null` at DB/API boundaries.
2. `resolveDisplayName(note)` is the only username fallback path for UI display; backend auth/delete behavior continues to use `username`.
3. `priority` defaults to `normal`; `high` and `urgent` are text-visible and use semantic color.
4. Memo creation and rendering behave exactly as before when no GPU is selected.
5. GPU selection reveals the compact hold inputs; no GPU selected means the memo path stays unchanged.
6. Full and Compact GPU cues use the shared frontend `noteAdvisory` helper to pick a deterministic primary hold and summarize the rest with `+N`.
7. Full `GpuBar` uses a custom hover/focus tooltip, not title-only hints; it has `role="tooltip"`, identical hover/focus content, and closes on leave, blur, and `Escape`.
8. Tooltip content includes GPU index/name, resolved display name, priority, expiry, and memo text/summary.
9. Advisory holds never alter GPU availability state.
10. The Full card keeps an exact `0.4rem` header-to-GPU-list gap.
11. Backend tests, frontend contract tests, `svelte-check`, build, and Playwright QA all pass with frontend commands run under NVM Node 24.
