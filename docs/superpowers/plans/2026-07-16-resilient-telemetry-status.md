# Resilient Telemetry and Failure-State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make I/O telemetry meaningful, detect partial GPU visibility with negligible added load, preserve manual order in Masonry, and present failure states, materials, and shortcuts coherently.

**Architecture:** Extend the existing ten-second remote system command with cheap kernel-counter reads, calculate deltas and inventory health in backend memory, and add backward-compatible optional payload fields. Keep frontend server order and telemetry truth authoritative while moving exception details into a material-aware card veil. Preserve existing Svelte actions, stores, and CSS layers.

**Tech Stack:** Python 3.10, FastAPI, SQLAlchemy, SvelteKit 5, TypeScript, Tailwind/CSS, Node 24 built-in test runner.

## Global Constraints

- Development repository only: /home/ircv/workspace/monitoring_v2_dev.
- Never edit, restart, or build the live repository /home/ircv/workspace/monitoring_v2.
- Keep backend and frontend collection cadence at ten seconds.
- Add no dependency, server daemon, lspci invocation, or extra SSH roundtrip.
- Preserve manual server order in Full and Compact.
- Existing payload fields remain backward compatible.
- Use test-first red, green, refactor cycles for each task.

---

## File map

- backend/collectors/system.py: remote kernel counter collection, parsing, disk-rate calculation.
- backend/collectors/gpu_health.py: pure GPU inventory assessment and mismatch debounce state.
- backend/collectors/server_collector.py: per-server previous samples, historical GPU baseline, status reason integration.
- backend/tests/test_system_metrics.py: disk counter and rate tests.
- backend/tests/test_gpu_health.py: visibility and debounce tests.
- frontend/src/lib/types.ts: optional disk_io and gpu_inventory payload contracts.
- frontend/src/lib/stores/servers.ts: normalization and equality.
- frontend/src/lib/utils/orderedMasonry.ts: monotonic ordered placement.
- frontend/src/lib/components/ServerCard.svelte: I/O semantics and failure veil copy.
- frontend/src/lib/styles/monitor-cards.css: material-aware veil and reveal behavior.
- frontend/src/lib/utils/devScenario.ts and frontend/src/routes/debug/+page.svelte: client-only fault simulation.
- frontend/src/lib/stores/theme.ts, frontend/src/app.css, frontend/src/lib/styles/monitor-dashboard.css, frontend/src/routes/+page.svelte: preset names, structural material variables, and shortcut discovery.

### Task 1: Kernel I/O counters and rates

**Files:**
- Modify: backend/collectors/system.py
- Modify: backend/tests/test_system_metrics.py

**Interfaces:**
- Produces SystemInfo.disk_read_bytes_total, disk_write_bytes_total, disk_sample_time, and pci_gpu_count.
- Produces calculate_disk_io_rate(previous, current) returning read_bytes_per_second and write_bytes_per_second or None.

- [ ] **Step 1: Write failing parser and delta tests**

Add tests that parse a ten-field payload, preserve the six-field payload, reject counter rollback, and calculate rates from elapsed monotonic time.

- [ ] **Step 2: Verify red**

Run:
~~~bash
PYTHONPATH=. .venv/bin/python -m unittest backend.tests.test_system_metrics -v
~~~

Expected: failures for missing fields and helper.

- [ ] **Step 3: Extend the existing remote command**

Read physical block counters from /proc/diskstats and NVIDIA display-class inventory from /sys/bus/pci/devices inside SYSTEM_CMD_PROC. Emit cumulative bytes, monotonic timestamp, and PCI GPU count in the same CSV line.

- [ ] **Step 4: Implement pure delta calculation**

The helper must return None for missing samples, non-positive elapsed time, or counter rollback. Rates must never be negative.

- [ ] **Step 5: Verify green and commit**

Run the backend unit command and commit:
~~~bash
git add backend/collectors/system.py backend/tests/test_system_metrics.py
git commit -m "feat: collect low-overhead disk io counters"
~~~

### Task 2: GPU visibility health

**Files:**
- Create: backend/collectors/gpu_health.py
- Create: backend/tests/test_gpu_health.py
- Modify: backend/collectors/server_collector.py

**Interfaces:**
- Produces GpuInventoryHealth with visible_count, expected_count, pci_count, missing_indices, and state.
- Server payload adds optional top-level gpu_inventory.
- Degraded reason code after two consecutive mismatches is gpu_device_missing.

- [ ] **Step 1: Write failing pure health tests**

Cover healthy inventory, PCI count greater than visible count, learned indices missing, one-sample suspect, two-sample missing, and recovery reset.

- [ ] **Step 2: Verify red**

Run:
~~~bash
PYTHONPATH=. .venv/bin/python -m unittest backend.tests.test_gpu_health -v
~~~

- [ ] **Step 3: Implement the pure inventory assessor**

Use visible NVIDIA indices, PCI display-controller count, and historical or learned indices. Never infer a missing GPU as available.

- [ ] **Step 4: Load historical baseline once**

On first collection, query distinct archived GpuMetric.gpu_index values for the server. This is one local database query per collector process, not a remote poll.

- [ ] **Step 5: Integrate two-sample debounce and status reason**

Visible rows remain in the payload. After two mismatches, set degraded with copy identifying visible versus expected GPUs and missing indices when known.

- [ ] **Step 6: Verify green and commit**

Run backend tests and commit:
~~~bash
git add backend/collectors/gpu_health.py backend/collectors/server_collector.py backend/tests/test_gpu_health.py
git commit -m "feat: detect partial gpu visibility"
~~~

### Task 3: Frontend telemetry contracts and I/O language

**Files:**
- Modify: frontend/src/lib/types.ts
- Modify: frontend/src/lib/stores/servers.ts
- Modify: frontend/src/lib/utils/serverStateMerge.test.ts
- Modify: frontend/src/lib/components/ServerCard.svelte
- Modify: frontend/src/lib/components/ServerCard.note-contract.test.ts

**Interfaces:**
- Consumes optional system.disk_read_bytes_per_second, disk_write_bytes_per_second, disk_sample_seconds.
- Consumes optional ServerState.gpu_inventory.

- [ ] **Step 1: Write failing normalization and card contract tests**

Require stable optional field normalization, equality checks, I/O 여유 for zero PSI and low throughput, I/O 병목 for pressure, and read/write throughput in expanded detail.

- [ ] **Step 2: Verify red**

Run Node 24 targeted tests with node --test.

- [ ] **Step 3: Implement semantic I/O formatting**

Never render a bare I/O 0%. Use idle, throughput, bottleneck, or unavailable copy while retaining exact PSI in the expanded detail.

- [ ] **Step 4: Verify green and commit**

~~~bash
git add frontend/src/lib/types.ts frontend/src/lib/stores/servers.ts frontend/src/lib/utils/serverStateMerge.test.ts frontend/src/lib/components/ServerCard.svelte frontend/src/lib/components/ServerCard.note-contract.test.ts
git commit -m "feat: clarify io telemetry semantics"
~~~

### Task 4: Monotonic ordered Masonry

**Files:**
- Modify: frontend/src/lib/utils/orderedMasonry.ts
- Modify: frontend/src/lib/utils/orderedMasonry.test.ts
- Modify only if necessary: frontend/src/routes/+page.svelte

**Interfaces:**
- placeOrderedMasonryItems keeps the current return shape.
- New invariant: row starts are non-decreasing in DOM order.

- [ ] **Step 1: Replace the round-robin expectation with failing monotonic tests**

Include uneven spans where the old algorithm returns row starts such as 1,1,1,3,4,2. Assert every later start is greater than or equal to the previous start and tie breaking is stable.

- [ ] **Step 2: Verify red**

Run the targeted orderedMasonry test and confirm the old algorithm fails.

- [ ] **Step 3: Implement stable shortest-column placement with a monotonic floor**

Choose the shortest column, leftmost on ties. Place at max(column next row, previous item row start), then advance the chosen column by span.

- [ ] **Step 4: Verify green and commit**

~~~bash
git add frontend/src/lib/utils/orderedMasonry.ts frontend/src/lib/utils/orderedMasonry.test.ts frontend/src/routes/+page.svelte
git commit -m "fix: preserve visual order in masonry"
~~~

### Task 5: Failure veil and stable card header

**Files:**
- Modify: frontend/src/lib/components/ServerCard.svelte
- Modify: frontend/src/lib/styles/monitor-cards.css
- Modify: frontend/src/lib/components/ServerCard.note-contract.test.ts
- Modify: frontend/src/routes/page-view.contract.test.ts

**Interfaces:**
- Card exposes data-operational-state and a dedicated monitor-card__state-veil.
- Header never renders relative freshness copy.

- [ ] **Step 1: Write failing structure and CSS contract tests**

Require condition-specific labels, no monitor-card__refresh baseline, veil blur, pointer hover and focus-within reveal, reduced-motion handling, and preserved availability truth.

- [ ] **Step 2: Verify red**

Run targeted frontend tests.

- [ ] **Step 3: Implement condition mapping**

Map status reason codes to SSH 연결 실패, GPU 인식 누락, 수집 지연, 메트릭 수집 실패, or 상태 확인 중. Put age only in secondary veil copy.

- [ ] **Step 4: Implement material-aware veil**

Keep the label above the blur. Reveal last-known data on hover or focus without making the card appear healthy.

- [ ] **Step 5: Verify green and commit**

~~~bash
git add frontend/src/lib/components/ServerCard.svelte frontend/src/lib/styles/monitor-cards.css frontend/src/lib/components/ServerCard.note-contract.test.ts frontend/src/routes/page-view.contract.test.ts
git commit -m "feat: add inspectable server failure veil"
~~~

### Task 6: Material identity and shortcut discovery

**Files:**
- Modify: frontend/src/lib/stores/theme.ts
- Modify: frontend/src/lib/stores/theme.contract.test.ts
- Modify: frontend/src/app.html
- Modify: frontend/src/app.css
- Modify: frontend/src/routes/+page.svelte
- Modify: frontend/src/routes/page-view.contract.test.ts
- Modify: frontend/src/lib/styles/monitor-dashboard.css

**Interfaces:**
- Cookie value liquid remains accepted for migration compatibility, but the user-facing label becomes Clean.
- Menu label becomes Theme / Material.
- Controls expose aria-keyshortcuts and data-shortcut-tooltip.

- [ ] **Step 1: Write failing label, material-variable, and shortcut tests**

Require Clean label, structural variables for all three presets, Theme / Material group label, V and C and 1/2/3 shortcut hints, and accessible shortcut attributes.

- [ ] **Step 2: Verify red**

Run targeted theme and page contract tests.

- [ ] **Step 3: Implement distinct structural material variables**

Clean is mostly opaque and crisp; Claude+ is warm and soft; AstroVista is cooler, tighter, and more technical. Reuse semantic tokens and avoid unrelated hard-coded component colors.

- [ ] **Step 4: Add contextual tooltip and menu legend**

Tooltips appear on hover or focus and never alter layout. The menu legend is one low-contrast row.

- [ ] **Step 5: Verify green and commit**

~~~bash
git add frontend/src/lib/stores/theme.ts frontend/src/lib/stores/theme.contract.test.ts frontend/src/app.html frontend/src/app.css frontend/src/routes/+page.svelte frontend/src/routes/page-view.contract.test.ts frontend/src/lib/styles/monitor-dashboard.css
git commit -m "feat: refine materials and shortcut discovery"
~~~

### Task 7: Development scenarios and complete verification

**Files:**
- Modify: frontend/src/lib/utils/devScenario.ts
- Modify: frontend/src/lib/utils/devScenario.test.ts
- Modify: frontend/src/routes/debug/+page.svelte
- Modify: frontend/src/routes/dev-scenario-integration.contract.test.ts

**Interfaces:**
- DEV_SCENARIOS adds gpu_missing.
- mixed covers stale, I/O pressure, offline, and GPU visibility mismatch without backend writes.

- [ ] **Step 1: Write failing scenario tests**

Require deterministic GPU missing state with visible count below expected count and gpu_device_missing reason. Preserve input immutability and server order.

- [ ] **Step 2: Verify red**

Run targeted scenario tests.

- [ ] **Step 3: Implement scenario and debug controls**

Add Korean scenario copy and keep all transformation session-scoped and client-only.

- [ ] **Step 4: Run complete verification**

~~~bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm use --silent 24
cd frontend
node --test $(find src -type f -name "*.test.ts" | sort)
npm run check
npm run build
cd ..
PYTHONPATH=. .venv/bin/python -m unittest discover -s backend/tests -v
git diff --check
~~~

- [ ] **Step 5: Browser QA**

Verify Full Grid and Masonry, offline hover and keyboard reveal, stale and GPU missing scenarios, all three materials in light and dark, shortcuts, 1440px desktop, and 390px mobile with no horizontal overflow.

- [ ] **Step 6: Independent review and final commit**

Run verifier and UX review, address only evidence-backed findings, confirm live HEAD remains c50f9d2 and clean, then commit remaining integration changes.
