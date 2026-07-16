# GPU Advisory Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task-by-task. Use checkbox (`- [ ]`) tracking.

**Goal:** Add priority and nullable display-name support to Note holds, keep memo behavior unchanged, and make Full/Compact GPU hold cues deterministic, accessible, and backward-compatible.

**Architecture:** Extend the Notes schema and router first, then add shared frontend helpers for API normalization, UI-only display-name fallback, and advisory hold ranking. Reuse the frontend `noteAdvisory` helper in Full and Compact surfaces so hold cues, tooltips, and `+N` summaries stay consistent while telemetry-driven availability remains untouched.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic v2, SQLite bootstrap helpers, SvelteKit 5, Svelte 5 runes, TypeScript, NVM Node 24 `node:test`, `npm run check`, `npm run build`, and Playwright CLI over the current DEV service.

## Global Constraints
- DEV repo only: `/home/ircv/workspace/monitoring_v2_dev`.
- Live repo `/home/ircv/workspace/monitoring_v2` must not be edited.
- Do not commit during this plan unless a later explicit instruction asks for commits.
- Memo default stays `memo`; hold priority default stays `normal`.
- `display_name` is nullable and capped at 40 trimmed characters.
- DB/API normalize omitted, `null`, empty, and whitespace-only `display_name` values to `null`; they do not replace them with `username`.
- UI display fallback to `username` is allowed only through `resolveDisplayName(note)` in the frontend advisory helper.
- Authorization, ownership checks, and delete behavior continue to use `username`.
- Advisory holds must never change GPU availability state.
- Ranking is a frontend advisory display concern only; do not add backend ranking behavior or backend ranking tests.
- No new dependencies, daemons, collector changes, or WebSocket changes.
- Use TDD: fail first, minimal fix, rerun.
- Every frontend command must explicitly activate NVM Node 24 before running.

## Frontend command prefix
Use this prefix for every frontend command in this plan:

```bash
cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend
```

## File map
- Backend: `backend/models.py`, `backend/database.py`, `backend/routers/notes.py`.
- Backend tests: `backend/tests/test_notes_validation.py`, `backend/tests/test_note_priority.py`.
- Frontend data: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/api.contract.test.ts`, `frontend/src/lib/utils/notePayload.ts`, `frontend/src/lib/utils/notePayload.test.ts`, `frontend/src/lib/utils/noteAdvisory.ts`, `frontend/src/lib/utils/noteAdvisory.test.ts`.
- Full view: `frontend/src/lib/components/NoteForm.svelte`, `frontend/src/lib/components/ServerCard.svelte`, `frontend/src/lib/components/GpuBar.svelte`, `frontend/src/lib/components/NoteForm.contract.test.ts`, `frontend/src/lib/components/ServerCard.note-contract.test.ts`, `frontend/src/lib/components/GpuBar.contract.test.ts`, `frontend/src/lib/styles/monitor-cards.css`.
- Compact view: `frontend/src/lib/components/CompactDashboard.svelte`, `frontend/src/lib/components/CompactServerRow.svelte`, `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`, `frontend/src/routes/page-view.contract.test.ts`, `frontend/src/lib/styles/monitor-compact.css`.

## Task 1: Backend schema, validation, and API normalization
**Files:** `backend/models.py`, `backend/database.py`, `backend/routers/notes.py`, `backend/tests/test_notes_validation.py`, `backend/tests/test_note_priority.py`

**Interfaces:**
- `Note.priority: Literal["normal", "high", "urgent"]` defaults to `normal`.
- `Note.display_name: str | None` is trimmed, capped at 40 characters, and stored/serialized as `null` when omitted, null, empty, or whitespace-only.
- Note list/create responses always serialize `priority`; old rows with missing priority normalize to `normal`.
- Note list/create responses serialize nullable raw `display_name`; fallback to `username` is not a backend responsibility.
- Delete/auth behavior remains based on `username` only.

- [ ] **Step 1: Write failing backend tests**
  - Add tests for schema backfill, `priority="normal"` defaulting, accepted priority values, invalid priority rejection, `display_name` trimming, whitespace-to-null normalization, nullable serialization, and 40-character length enforcement.
  - Add tests that auth/delete still use `username` and do not depend on `display_name`.
  - Do not add backend ranking tests.

- [ ] **Step 2: Run the red backend suite**
  - Run:
    ```bash
    cd /home/ircv/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_notes_validation backend.tests.test_note_priority -v
    ```
  - Expected: failures for missing columns/validators/serialization.

- [ ] **Step 3: Implement the minimal backend changes**
  - Add the new columns, SQLite bootstrap/backfill behavior, Pydantic validation, and note serialization in `backend/routers/notes.py`.
  - Normalize `display_name` to nullable raw display text and keep `username` untouched for ownership/delete logic.

- [ ] **Step 4: Re-run backend verification**
  - Run:
    ```bash
    cd /home/ircv/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_notes_validation backend.tests.test_note_priority backend.tests.test_note_admin_override -v
    ```
  - Expected: all backend note tests pass.

## Task 2: Shared frontend API contracts and advisory helper
**Files:** `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/api.contract.test.ts`, `frontend/src/lib/utils/notePayload.ts`, `frontend/src/lib/utils/notePayload.test.ts`, `frontend/src/lib/utils/noteAdvisory.ts`, `frontend/src/lib/utils/noteAdvisory.test.ts`

**Interfaces:**
- `Note` and `NoteCreatePayload` gain `priority` and nullable `display_name` fields.
- API normalization maps old responses without `priority` to `priority: "normal"`.
- API normalization maps missing, null, empty, and whitespace-only `display_name` to `display_name: null`; it does not fallback to `username`.
- `noteAdvisory.ts` owns `resolveDisplayName(note)`, priority labels/classes, and deterministic primary-hold ranking for Full and Compact.

- [ ] **Step 1: Write failing Node tests for API normalization, payloads, and ranking**
  - In `frontend/src/lib/api.contract.test.ts`, add an old-response test proving omitted `priority` becomes `normal` and raw `display_name` values `null`, missing, empty, and whitespace normalize to `null`.
  - In `notePayload.test.ts`, cover create-payload priority default, display-name trimming, whitespace-to-null, max-40 validation, and no username fallback in payload normalization.
  - In `noteAdvisory.test.ts`, cover `resolveDisplayName(note)` fallback to `username`, priority label/class output, deterministic ordering `urgent > high > normal`, soonest expiry, stable tie-break, and `+N` summarization inputs.

- [ ] **Step 2: Run the red frontend unit tests with NVM Node 24**
  - Run:
    ```bash
    cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend && node --experimental-strip-types --test src/lib/api.contract.test.ts src/lib/utils/notePayload.test.ts src/lib/utils/noteAdvisory.test.ts
    ```
  - Expected: failures until the new fields/helpers exist.

- [ ] **Step 3: Implement the shared data layer**
  - Extend TypeScript types and API normalization.
  - Add the pure `noteAdvisory` helper for `resolveDisplayName`, priority metadata, primary-hold selection, and secondary-count support.
  - Keep ranking out of the backend.

- [ ] **Step 4: Re-run unit checks and static typing with NVM Node 24**
  - Run:
    ```bash
    cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend && node --experimental-strip-types --test src/lib/api.contract.test.ts src/lib/utils/notePayload.test.ts src/lib/utils/noteAdvisory.test.ts && npm run check
    ```
  - Expected: helper/API tests pass and Svelte type-check stays green.

## Task 3: Full card note composer, GpuBar tooltip, and semantic styling
**Files:** `frontend/src/lib/components/NoteForm.svelte`, `frontend/src/lib/components/ServerCard.svelte`, `frontend/src/lib/components/GpuBar.svelte`, `frontend/src/lib/components/NoteForm.contract.test.ts`, `frontend/src/lib/components/ServerCard.note-contract.test.ts`, `frontend/src/lib/components/GpuBar.contract.test.ts`, `frontend/src/lib/styles/monitor-cards.css`

**Interfaces:**
- GPU selection reveals the compact hold inputs only when at least one GPU is selected.
- Full GPU cue text uses the shared `noteAdvisory` helper and `resolveDisplayName(note)`.
- Normal priority stays neutral; high/urgent are text-visible with semantic colors.
- Full `GpuBar` uses a custom tooltip, not `title`-only hints.
- Hover and keyboard focus reveal identical tooltip content with `role="tooltip"`.
- Tooltip closes on pointer leave, focus blur, and `Escape`.
- Tooltip content includes GPU index/name, resolved display name, priority, expiry, and memo text/summary.
- The card header-to-GPU-list gap is exactly `0.4rem`.

- [ ] **Step 1: Write failing Full-view contract tests first**
  - Assert the memo path stays unchanged with no GPU selected.
  - Assert `display_name` is optional, capped at 40 trimmed characters, and not used for ownership/delete behavior.
  - Assert hold-specific inputs appear only after GPU selection.
  - Assert the GPU cue uses `noteAdvisory` ranking and `resolveDisplayName(note)` fallback.
  - Assert `GpuBar` does not rely on `title`-only hints and exposes a custom `role="tooltip"` on hover and focus.
  - Assert tooltip content includes GPU index/name, resolved display name, priority, expiry, and memo text/summary.
  - Assert leave, blur, and `Escape` close the tooltip.
  - Assert the Full card header-to-GPU-list gap is exactly `0.4rem`.

- [ ] **Step 2: Run the red Full-view test command with NVM Node 24**
  - Run:
    ```bash
    cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend && node --experimental-strip-types --test src/lib/components/NoteForm.contract.test.ts src/lib/components/ServerCard.note-contract.test.ts src/lib/components/GpuBar.contract.test.ts
    ```
  - Expected: failures until the new fields/copy/styles/tooltip behavior are implemented.

- [ ] **Step 3: Implement the minimal Full-view changes**
  - Wire `priority` and nullable `display_name` into the composer and note preview.
  - Update `ServerCard`/`GpuBar` to use `noteAdvisory` for primary-hold selection, `+N`, priority metadata, and display-name fallback.
  - Implement the custom Full `GpuBar` tooltip with hover/focus parity and close handling for leave, blur, and `Escape`.
  - Keep availability state derived only from telemetry.
  - Set the Full card header-to-GPU-list gap to exactly `0.4rem`.

- [ ] **Step 4: Re-run frontend checks for the Full card with NVM Node 24**
  - Run:
    ```bash
    cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend && node --experimental-strip-types --test src/lib/components/NoteForm.contract.test.ts src/lib/components/ServerCard.note-contract.test.ts src/lib/components/GpuBar.contract.test.ts && npm run check && npm run build
    ```
  - Expected: contract tests pass, then check/build pass.

## Task 4: Compact view cues, page contracts, and Playwright QA
**Files:** `frontend/src/lib/components/CompactDashboard.svelte`, `frontend/src/lib/components/CompactServerRow.svelte`, `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`, `frontend/src/routes/page-view.contract.test.ts`, `frontend/src/lib/styles/monitor-compact.css`

**Interfaces:**
- Compact hold cues use the same `noteAdvisory` ranking/fallback helper as Full.
- Tooltip text stays non-interactive, keyboard reachable, and availability-neutral.
- QA uses the current DEV frontend on `http://127.0.0.1:5174`; do not switch to another port unless 5174 is unavailable and the plan is updated.

- [ ] **Step 1: Write failing Compact-view tests**
  - Cover display-name fallback through `resolveDisplayName(note)`, `urgent/high/normal` ranking through `noteAdvisory`, `+N` summarization, hover/focus tooltip parity, and no availability changes from holds.

- [ ] **Step 2: Run the red Compact-view command with NVM Node 24**
  - Run:
    ```bash
    cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend && node --experimental-strip-types --test src/lib/components/compact-dashboard-task4.contract.test.ts src/routes/page-view.contract.test.ts
    ```
  - Expected: failures until the compact cue logic and CSS match the new contract.

- [ ] **Step 3: Implement the minimal Compact changes**
  - Reuse `noteAdvisory.ts` in `CompactDashboard.svelte` and `CompactServerRow.svelte`.
  - Keep the row state and telemetry semantics unchanged.

- [ ] **Step 4: Re-run frontend checks with NVM Node 24**
  - Run:
    ```bash
    cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend && node --experimental-strip-types --test src/lib/components/compact-dashboard-task4.contract.test.ts src/routes/page-view.contract.test.ts && npm run check && npm run build
    ```
  - Expected: Compact contract tests, `svelte-check`, and build all pass.

- [ ] **Step 5: Playwright QA against current DEV 5174**
  - Use the current DEV frontend at `http://127.0.0.1:5174`.
  - If running from a local workstation, tunnel the remote DEV port without touching LIVE:
    ```bash
    ssh -p 2200 -N -L 5174:127.0.0.1:5174 ircv@166.104.167.11
    ```
  - Use the bundled wrapper:
    ```bash
    PLAYWRIGHT_CLI_SESSION=gpu-advisory-priority bash /Users/shchoi/.codex/skills/playwright/scripts/playwright_cli.sh open http://127.0.0.1:5174
    ```
  - Capture desktop and mobile screenshots for both Full and Compact views.
  - Verify fallback display names, hover/focus tooltips, leave/blur/`Escape` close behavior, semantic priority colors, unchanged availability, `+N` summaries, and exact `0.4rem` Full card header-to-GPU-list gap.

## Final verification
- [ ] Run backend note tests:
  ```bash
  cd /home/ircv/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_notes_validation backend.tests.test_note_priority backend.tests.test_note_admin_override -v
  ```
- [ ] Run frontend unit and contract tests with NVM Node 24:
  ```bash
  cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend && node --experimental-strip-types --test src/lib/api.contract.test.ts src/lib/utils/notePayload.test.ts src/lib/utils/noteAdvisory.test.ts src/lib/components/NoteForm.contract.test.ts src/lib/components/ServerCard.note-contract.test.ts src/lib/components/GpuBar.contract.test.ts src/lib/components/compact-dashboard-task4.contract.test.ts src/routes/page-view.contract.test.ts
  ```
- [ ] Run frontend static checks and build with NVM Node 24:
  ```bash
  cd /home/ircv/workspace/monitoring_v2_dev && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use --silent 24 && cd frontend && npm run check && npm run build
  ```
- [ ] Run Playwright QA against `http://127.0.0.1:5174` with `bash /Users/shchoi/.codex/skills/playwright/scripts/playwright_cli.sh` and save desktop/mobile screenshots.
- [ ] Run whitespace diff validation:
  ```bash
  cd /home/ircv/workspace/monitoring_v2_dev && git diff --check
  ```

## Self-review checklist
- [ ] Every required behavior maps to exactly one backend, shared-data, Full, or Compact task.
- [ ] All file paths are exact and limited to the note-priority surface.
- [ ] The plan includes fail-first tests and rerun commands but no commit steps.
- [ ] Every frontend command activates NVM Node 24.
- [ ] API tests cover old response `priority="normal"` and raw nullable `display_name` normalization.
- [ ] Display-name fallback is UI-only through `resolveDisplayName(note)`.
- [ ] Ranking is handled only by the frontend `noteAdvisory` helper; backend ranking tests are absent.
- [ ] Full `GpuBar` tooltip behavior covers hover/focus parity, `role="tooltip"`, leave/blur/`Escape` close, GPU index/name, priority, expiry, and memo content.
- [ ] Playwright QA targets current DEV `http://127.0.0.1:5174`, uses the specified wrapper, and captures desktop/mobile screenshots.
- [ ] Full card gap is exactly `0.4rem`.
