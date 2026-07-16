// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./ServerCard.svelte', import.meta.url), 'utf8');

test('ServerCard renders advisory hold chips and wires the dense NoteForm props', () => {
	assert.match(source, /note\.kind === 'hold'/);
	assert.match(source, /note\.gpu_indices/);
	assert.match(source, /monitor-note-item__kind">HOLD<\/span>/);
	assert.match(
		source,
		/<NoteForm[\s\S]*serverId=\{server\.server_id\}[\s\S]*gpus=\{server\.gpus\}[\s\S]*serverStatus=\{server\.status\}[\s\S]*lastSeen=\{server\.last_seen\}[\s\S]*active=\{notesExpanded\}[\s\S]*onCreated=\{onNoteCreated\}/
	);
});

test('ServerCard derives active unexpired hold notes per GPU and passes cues into GpuBar', () => {
	assert.match(source, /activeHoldNotesByGpu\s*=\s*\$derived\.by/, 'missing derived per-GPU hold cue map');
	assert.match(source, /note\.kind\s*===\s*'hold'/, 'hold cue map should only use hold notes');
	assert.match(source, /noteVisible\(note\)/, 'hold cue map should ignore expired notes');
	assert.match(source, /holdGpuIndices\(note\)/, 'hold cue map should validate GPU indices');
	const gpuBar = source.match(/<GpuBar[\s\S]*?\/>/)?.[0] ?? '';
	assert.match(gpuBar, /\{gpu\}/, 'GPU row should receive its telemetry record');
	assert.match(
		gpuBar,
		/state=\{operationalState === 'impaired' \? staleAvailabilityState : getCompactGpuState\(server\.status, server\.last_seen, gpu\)\}/,
		'GPU row should receive shared availability state and unknown impaired override'
	);
	assert.match(
		gpuBar,
		/advisoryHolds=\{activeHoldNotesByGpu\[gpu\.index\]\s*\?\?\s*\[\]\}/,
		'each GPU row should receive only its own hold cues'
	);
});

test('ServerCard header keeps one stable baseline with inline host and edit controls', () => {
	const titleRow = source.match(/<header class="monitor-card__header">[\s\S]*?<\/header>/)?.[0] ?? '';
	assert.match(titleRow, /monitor-card__title-line/, 'title row should keep one inline identity line');
	assert.match(titleRow, /monitor-card__status/, 'title row should keep the accessible health status');
	assert.match(titleRow, /monitor-card__host/, 'host should live in the inline identity line');
	assert.doesNotMatch(titleRow, /monitor-card__refresh|refreshText|statusReasonText|monitor-card__reason/, 'freshness and reason copy should not live in the stable header');
	assert.match(titleRow, /monitor-card__edit-button/, 'edit control should stay in the compact header row');
	assert.doesNotMatch(source, /monitor-card__meta/, 'separate meta row should be removed');
	assert.doesNotMatch(source, /monitor-card__network/, 'decorative network chip should not remain in the header');
	assert.match(source, /const endpointText = \$derived/, 'visible endpoint should be derived once');
	assert.match(source, /server\.port !== DEFAULT_SSH_PORT/, 'non-default SSH ports must remain visible');
	assert.match(source, /\{endpointText\}/, 'the compact header should render the operational endpoint');
	assert.match(source, /freshnessIssueText\(server\.last_seen, nowMs\)/, 'freshness should be derived from the shared page clock as exception-only copy');
	assert.match(source, /FRESHNESS_WARNING_AFTER_MS/, 'healthy sub-threshold updates must not emit second-by-second copy');
	assert.match(source, /stateVeilSecondary/, 'freshness details should move to the state veil secondary copy');
	assert.doesNotMatch(source, /const refreshText = \$derived\(lastSeenAbsoluteText\)/);
});

test('collapsed System and Memo controls rely on the disclosure chevron only', () => {
	assert.doesNotMatch(source, /monitor-card__footer-marker/, 'decorative footer markers should be removed from markup');
	assert.match(source, /monitor-card__footer-disclosure/, 'dense chevron affordance must remain');
	assert.doesNotMatch(source, />\s*[▾▸]\s*</);
});

test('System and Memo disclosure panels stay mounted while closed with accessible controls', () => {
	assert.match(source, /aria-expanded=\{sysExpanded\}/, 'System disclosure button should expose current expanded state');
	assert.match(source, /aria-controls=\{`system-panel-\$\{server\.server_id\}`\}/, 'System disclosure button should control the mounted panel');
	assert.match(source, /aria-expanded=\{notesExpanded\}/, 'Memo disclosure button should expose current expanded state');
	assert.match(source, /aria-controls=\{`notes-panel-\$\{server\.server_id\}`\}/, 'Memo disclosure button should control the mounted panel');

	const systemSection = source.match(/<section class="monitor-card__footer-section">[\s\S]*?system-panel-\$\{server\.server_id\}`[\s\S]*?<\/section>/)?.[0] ?? '';
	assert.match(systemSection, /id=\{`system-panel-\$\{server\.server_id\}`\}/, 'System panel should be present in the section markup');
	assert.doesNotMatch(systemSection, /\{#if sysExpanded\}[\s\S]*id=\{`system-panel-/, 'System panel must not be conditionally unmounted when closed');
	assert.match(systemSection, /aria-hidden=\{!sysExpanded\}/, 'closed System panel should be hidden from assistive tech');
	assert.match(systemSection, /inert=\{!sysExpanded\}/, 'closed System panel should make hidden descendants unfocusable');

	const notesSection = source.match(/<section class="monitor-card__footer-section">[\s\S]*?notes-panel-\$\{server\.server_id\}`[\s\S]*?<\/section>/)?.[0] ?? '';
	assert.match(notesSection, /id=\{`notes-panel-\$\{server\.server_id\}`\}/, 'Memo panel should be present in the section markup');
	assert.doesNotMatch(notesSection, /\{#if notesExpanded\}[\s\S]*id=\{`notes-panel-/, 'Memo panel must not be conditionally unmounted when closed');
	assert.match(notesSection, /aria-hidden=\{!notesExpanded\}/, 'closed Memo panel should be hidden from assistive tech');
	assert.match(notesSection, /inert=\{!notesExpanded\}/, 'closed Memo panel should make hidden controls unfocusable');
});

test('System and Memo share the mounted disclosure shell and inner panel pattern', () => {
	const shells = source.match(/class="monitor-card__disclosure-shell"/g) ?? [];
	assert.equal(shells.length, 2, 'System and Memo should both use the shared mounted shell');
	const inners = source.match(/class="monitor-card__disclosure-inner monitor-card__footer-panel"/g) ?? [];
	assert.equal(inners.length, 2, 'System and Memo should both keep content in the shared inner panel');
	assert.match(source, /data-expanded=\{sysExpanded \? 'true' : 'false'\}/, 'System shell should expose state for CSS motion');
	assert.match(source, /data-expanded=\{notesExpanded \? 'true' : 'false'\}/, 'Memo shell should expose state for CSS motion');
});

test('ServerCard hides the normal status label while keeping exception labels visible', () => {
	assert.match(source, /statusMeta\.label === '정상'/);
	assert.match(source, /class="monitor-card__status-text monitor-card__sr-only"/);
	assert.match(source, /\{:else\}\s*<span class="monitor-card__status-text">\{statusMeta\.label\}<\/span>/);
});

test('ServerCard renders collapsed System as one resource-leading CPU RAM Storage load line', () => {
	const preview = source.match(/<span class="monitor-card__footer-preview monitor-card__system-preview">[\s\S]*?monitor-card__footer-disclosure/)?.[0] ?? '';
	const cpuIndex = preview.indexOf('CPU {cpuPreviewText}');
	const ramIndex = preview.indexOf('RAM {ramPreviewText}');
	const storageIndex = preview.indexOf('Storage {storagePreviewText}');
	const loadIndex = preview.indexOf('monitor-card__load-preview');

	assert.ok(cpuIndex >= 0 && cpuIndex < ramIndex && ramIndex < storageIndex && storageIndex < loadIndex, 'CPU, RAM and Storage must remain visible before secondary load context');
	assert.doesNotMatch(preview, /monitor-card__load-gauge/, 'normal collapsed summary should not spend width on a load gauge');
	assert.match(preview, /monitor-card__load-text">\{loadPreviewText\}<\/span>/, 'collapsed system preview should retain compact load text');
	assert.match(preview, /#each loadPreviewCauses as cause/, 'collapsed system preview should append only active cause labels');
	assert.match(preview, /· \{cause\.label\}/, 'pressure cause labels should be separated from the dense summary');
	assert.match(source, /const ramPreviewText = \$derived\([\s\S]*?`\$\{ramPct\.toFixed\(0\)\}%`/, 'collapsed RAM preview should use percentage only');
	assert.match(source, /const storagePreviewText = \$derived\([\s\S]*?`\$\{storagePct\.toFixed\(0\)\}%`/, 'collapsed Storage preview should use percentage only');
	assert.match(source, /CPU 압박/);
	assert.match(source, /CPU 병목/);
	assert.match(source, /I\/O 압박/);
	assert.match(source, /I\/O 병목/);
	assert.match(source, /마지막 ·/, 'historical collapsed preview should prefix the last-sample cue');
	assert.doesNotMatch(preview, /monitor-card__system-preview-segment/, 'old segmented preview pills should be removed');
	assert.doesNotMatch(source, /monitor-card__system-preview-item/, 'old 4-tile micro-grid should be removed');
});

test('expanded System keeps load pressure RAM and Storage capacity facts without throughput rows', () => {
	assert.match(source, /monitor-card__io-detail/, 'expanded system should expose a dedicated dense pressure detail row');
	assert.match(source, /monitor-card__io-detail-metrics/, 'expanded system should keep the pressure metrics inline');
	assert.match(source, /ioPressureHelpText/, 'expanded system should provide a tooltip or title explaining stall pressure');
	assert.match(source, /loadDetailText/, 'expanded system should surface 1\/5\/15 load detail text');
	assert.match(source, /cpuCountText/, 'expanded system should surface logical CPU count');
	assert.match(source, /cpuRunningText/, 'expanded system should surface runnable task count');
	assert.match(source, /ramSystemDetailText/, 'expanded system should surface RAM utilization and capacity');
	assert.match(source, /storageSystemDetailText/, 'expanded system should surface Storage utilization and capacity');
	assert.match(source, /cpuPressureSomeText/, 'expanded system should surface CPU PSI some avg10');
	assert.match(source, /ioSomeText/, 'expanded system should surface I\/O PSI some avg10');
	assert.match(source, /ioFullText/, 'expanded system should surface I\/O PSI full avg10');
	assert.match(source, /ioBlockedText/, 'expanded system should surface blocked task count');
	const facts = source.match(/<div class="monitor-card__system-facts">[\s\S]*?<\/div>/)?.[0] ?? '';
	assert.match(facts, /<small>부하<\/small><strong>\{loadDetailText\}<\/strong>/);
	assert.match(facts, /<small>논리 CPU<\/small><strong>\{cpuCountText\}<\/strong>/);
	assert.match(facts, /<small>CPU<\/small><strong>\{cpuSystemDetailText\}<\/strong>/);
	assert.match(facts, /<small>RAM<\/small><strong>\{ramSystemDetailText\}<\/strong>/);
	assert.match(facts, /<small>Storage<\/small><strong>\{storageSystemDetailText\}<\/strong>/);
	assert.match(facts, /<small>실행대기<\/small><strong>\{cpuRunningText\}<\/strong>/);
	assert.doesNotMatch(source, /monitor-card__system-summary/, 'old summary tile grid should be removed from the dense system panel');
	assert.doesNotMatch(source, /monitor-card__summary-item/, 'old summary tiles should not remain');
	assert.doesNotMatch(source, /MB\/s|diskReadText|diskWriteText|<span>R<\/span>|<span>W<\/span>/, 'System panel must not render disk throughput');
});

test('collapsed memo preview uses explicit Korean relative expiry instead of cryptic countdown tokens', () => {
	const preview = source.match(
		/class="monitor-card__footer-preview monitor-card__footer-preview--notes"[\s\S]*?monitor-card__footer-disclosure/
	)?.[0] ?? '';
	assert.match(preview, /monitor-card__note-preview-main/);
	assert.match(preview, /monitor-card__note-preview-user/);
	assert.match(preview, /monitor-card__note-preview-content/);
	assert.match(preview, /monitor-card__note-preview-expiry/);
	assert.match(preview, /\{noteRemainingText\(previewNotes\[0\]\)\}/, 'collapsed memo preview should use explicit Korean relative expiry');
	assert.match(preview, /monitor-card__note-preview-hold/, 'hold preview should lead with explicit HOLD GPU scope');
	assert.match(preview, /@\{previewNotes\[0\]\.username\}/, 'memo owner must be unmistakable as a user identity');
	assert.match(preview, /title=\{previewNotes\[0\]\.content\}/, 'truncated memo content must retain its full text');
	assert.doesNotMatch(source, /notePreviewCountdownText/, 'cryptic D\/H\/M\/S countdown helper should be removed');
});

test('note expiry helper speaks in concise Korean relative units and expired state', () => {
	assert.match(source, /return '만료됨';/, 'expired notes should say 만료됨');
	assert.ok(source.includes('return `${seconds}초`;'));
	assert.ok(source.includes('return `${minutes}분`;'));
	assert.ok(source.includes('return `${hours}시간`;'));
	assert.ok(source.includes('return `${days}일`;'));
	assert.doesNotMatch(source, /`\$\{(?:seconds|minutes|hours|days)\}(?:초|분|시간|일) 남음`/);
});

test('Task 4 ServerCard consumes the page shared nowMs clock and owns no interval', () => {
	assert.match(source, /nowMs:\s*number/, 'ServerCard should accept a parent nowMs prop');
	assert.match(source, /freshnessIssueText\(server\.last_seen, nowMs\)/, 'freshness should use the parent clock');
	assert.match(source, /expiresAtMs > nowMs/, 'note visibility should use the parent clock');
	assert.match(source, /return expiresAtMs - nowMs/, 'note remaining time should use the parent clock');
	assert.doesNotMatch(source, /noteNowMs/, 'per-card noteNowMs state should be removed');
	assert.doesNotMatch(source, /\bsetInterval\s*\(/, 'ServerCard must not fan out one timer per card');
	assert.doesNotMatch(source, /\bclearInterval\s*\(/, 'ServerCard should not own an interval cleanup');
});

test('ServerCard note preview and history keep concise HOLD markers and GPU chips', () => {
	assert.doesNotMatch(source, /advisory soft hold/);
	assert.match(source, /monitor-note-item__kind">HOLD<\/span>/);
	assert.match(source, /monitor-note-item__gpu-chip">G\{gpuIndex\}<\/span>/);
	assert.match(source, /monitor-note-item__user">@\{note\.username\}<\/span>/, 'history must distinguish author from memo copy');
});

test('ServerCard separates memo history from the composer and provides a deliberate empty state', () => {
	assert.match(source, /monitor-card__memo-group monitor-card__memo-group--history/);
	assert.match(source, /monitor-card__memo-group monitor-card__memo-group--composer/);
	assert.match(source, /monitor-card__memo-group-title">기록<\/span>/);
	assert.match(source, /monitor-card__memo-group-title">작성<\/span>/);
	assert.match(source, /monitor-card__memo-empty/);
});

test('ServerCard exposes a non-text availability nudge without reordering cards', () => {
	assert.ok(source.includes('const availableGpuCount = $derived.by'));
	assert.match(source, /getCompactGpuState\(server\.status, server\.last_seen, gpu\)[\s\S]*=== 'available'/);
	assert.match(source, /operationalState === 'impaired' \? staleAvailabilityState/, 'impaired availability should not advertise free GPUs');
	assert.ok(source.includes('const hasAvailableGpu = $derived(availableGpuCount > 0)'));
	assert.ok(source.includes("data-has-available={hasAvailableGpu ? 'true' : 'false'}"));
});


test('ServerCard derives CPU and I/O cause labels only when pressure is non-idle', () => {
	assert.match(source, /const cpuPressureLevel = \$derived\(classifyPressure\(cpuPressureSome\)\)/);
	assert.match(source, /const ioPressureLevel = \$derived\.by/, 'I\/O pressure should be derived from PSI and blocked-task telemetry');
	assert.match(source, /const loadPreviewCauses = \$derived\.by/, 'collapsed preview should gather only active cause labels');
	assert.match(source, /pressureLabel\(cpuPressureLevel\)/, 'CPU copy should reuse the semantic pressure label helper');
	assert.match(source, /pressureLabel\(ioPressureLevel\)/, 'I\/O copy should reuse the semantic pressure label helper');
	assert.doesNotMatch(source, /CPU 여유|I\/O 여유/, 'collapsed preview must not render healthy cause labels');
});

test('ServerCard removes throughput fallback and any MB/s copy from the System UI', () => {
	assert.doesNotMatch(source, /formatDiskThroughput/, 'System UI must not keep an MB\/s formatter');
	assert.doesNotMatch(source, /diskReadBytesPerSecond|diskWriteBytesPerSecond|diskThroughputBytesPerSecond/, 'System UI must not derive disk throughput preview values');
	assert.doesNotMatch(source, /MB\/s/, 'System UI copy must not render MB\/s anywhere');
});

test('expanded System pressure detail shows CPU and I/O PSI facts without R/W throughput cells', () => {
	assert.match(source, /monitor-card__io-detail-table/, 'expanded pressure details should keep stable tabular formatting');
	assert.match(source, /<span>CPU PSI<\/span><strong>\{cpuPressureSomeText\}<\/strong>/, 'expanded details should show CPU PSI some');
	assert.match(source, /<span>I\/O some<\/span><strong>\{ioSomeText\}<\/strong>/, 'expanded details should show I\/O PSI some');
	assert.match(source, /<span>I\/O full<\/span><strong>\{ioFullText\}<\/strong>/, 'expanded details should show I\/O PSI full');
	assert.match(source, /<span>blocked<\/span><strong>\{ioBlockedText\}<\/strong>/, 'expanded details should show blocked tasks');
	assert.doesNotMatch(source, /<span>R<\/span>|<span>W<\/span>/, 'expanded details must not show read\/write throughput cells');
});


test('Task 5 ServerCard keeps header stable and moves freshness/reason copy into a non-interactive veil', () => {
	const header = source.match(/<header class="monitor-card__header">[\s\S]*?<\/header>/)?.[0] ?? '';
	assert.ok(header, 'missing card header');
	assert.doesNotMatch(header, /monitor-card__refresh|refreshText|statusReasonText|monitor-card__reason/, 'header must not render relative freshness or reason rows');
	assert.match(header, /monitor-card__title/, 'header keeps server name');
	assert.match(header, /monitor-card__status/, 'header keeps status');
	assert.match(header, /monitor-card__host/, 'header keeps host');
	assert.match(header, /monitor-card__edit-button/, 'header keeps edit affordance');

	assert.match(source, /data-operational-state=\{operationalState\}/, 'card exposes stable operational state for CSS/testing');
	assert.match(source, /class="monitor-card__body"/, 'GPU and footer content must be wrapped as the veiled body');
	assert.match(source, /class="monitor-card__state-veil"/, 'non-healthy state must render a dedicated state veil');
	assert.match(source, /\{stateVeilLabel\}/, 'state veil should own the compact label');
	assert.match(source, /\{stateVeilSecondary\}/, 'secondary reason and age belong inside the veil');
	assert.doesNotMatch(source, /<p class="monitor-card__reason"/, 'legacy reason row must be removed');
});

test('Task 5 ServerCard maps status reasons to bounded Korean veil labels and preserves unknown availability', () => {
	assert.match(source, /function stateVeilLabelFor/, 'veil labels should use a dedicated reason mapper');
	for (const label of ['GPU 인식 누락', '수집 지연', '수집 중단', 'SSH 연결 실패', 'GPU 메트릭 수집 실패', '시스템 메트릭 수집 실패', '상태 확인 중', '메트릭 수집 실패']) {
		assert.ok(source.includes(label), `missing veil label ${label}`);
	}
	assert.match(source, /case 'gpu_device_missing':[\s\S]*return 'GPU 인식 누락'/);
	assert.match(source, /case 'stale_snapshot':[\s\S]*return '수집 지연'/);
	assert.match(source, /case 'stale_offline':[\s\S]*return '수집 중단'/);
	assert.match(source, /case 'gpu_collect_failed':[\s\S]*return 'GPU 메트릭 수집 실패'/);
	assert.match(source, /case 'system_collect_failed':[\s\S]*return '시스템 메트릭 수집 실패'/);
	assert.match(source, /case 'unknown':[\s\S]*return '상태 확인 중'/);
	assert.match(source, /status === 'degraded'[\s\S]*return '메트릭 수집 실패'/, 'degraded fallback should be metrics collection failure');
	assert.match(source, /offline|connect_failed|connection_failed|ssh_connect_failed/, 'offline/connect failures should collapse to SSH failure');
	assert.match(source, /const staleAvailabilityState = \$derived\('unknown'\)/, 'offline/stale availability remains unknown instead of healthy/unavailable');
});

test('ServerCard treats gpu_device_missing with stale refresh copy as historical system telemetry', () => {
	assert.match(source, /function isHistoricalSystemTelemetryStatus\(status: ServerStatus, reasonCode: string \| null, refreshText: string\): boolean/);
	assert.match(source, /case 'stale_snapshot':[\s\S]*case 'dev-sim-stale':[\s\S]*case 'stale_offline':[\s\S]*case 'system_collect_failed':[\s\S]*case 'offline':[\s\S]*case 'connect_failed':[\s\S]*case 'connection_failed':[\s\S]*case 'ssh_connect_failed':[\s\S]*case 'dev-sim-offline':[\s\S]*return true/);
	assert.doesNotMatch(source, /if \(reasonCode === 'gpu_device_missing'\) return false;/, 'gpu_device_missing must not bypass historical placeholders when refreshText is non-empty');
	assert.match(source, /return status === 'offline' \|\| status === 'unknown' \|\| Boolean\(refreshText\);/, 'non-empty refreshText should make gpu_device_missing historical while current degradation remains non-historical');
	assert.match(source, /const isHistoricalSystemTelemetry = \$derived\(isHistoricalSystemTelemetryStatus\(server\.status, statusReasonCode, refreshText\)\)/);
});

test('historical and offline System preview keeps a neutral last-sample load cue', () => {
	assert.match(source, /const systemPreviewUnavailableText = '–';/);
	assert.match(source, /const loadPreviewPrefixText = \$derived/);
	assert.match(source, /const loadPreviewText = \$derived\.by/, 'collapsed preview should derive compact load context');
	assert.match(source, /\{#if isHistoricalSystemTelemetry && server\.system\}[\s\S]*monitor-card__last-sample-label[\s\S]*마지막 수집값[\s\S]*\{\/if\}/);
	assert.match(source, /마지막 ·/, 'historical preview should prefix the last-sample cue');
	assert.match(source, /부하 – \/ –/, 'neutral fallback copy should remain available when load is unusable');
});

test('expanded historical System keeps raw last-sample load and pressure fact values', () => {
	const facts = source.match(/<div class="monitor-card__system-facts">[\s\S]*?<\/div>/)?.[0] ?? '';
	const ioDetail = source.match(/<div class="monitor-card__io-detail"[\s\S]*?<\/div>/)?.[0] ?? '';
	assert.match(source, /const loadDetailText = \$derived/);
	assert.match(source, /const cpuSystemDetailText = \$derived\(server\.system \? `\$\{cpuPct\.toFixed\(0\)\}%` : systemPreviewUnavailableText\)/);
	assert.match(source, /const cpuRunningText = \$derived/);
	assert.match(source, /const cpuPressureSomeText = \$derived/);
	assert.match(facts, /<small>부하<\/small><strong>\{loadDetailText\}<\/strong>/);
	assert.match(facts, /<small>CPU<\/small><strong>\{cpuSystemDetailText\}<\/strong>/);
	assert.match(facts, /<small>RAM<\/small><strong>\{ramSystemDetailText\}<\/strong>/);
	assert.match(facts, /<small>Storage<\/small><strong>\{storageSystemDetailText\}<\/strong>/);
	assert.match(ioDetail, /<span>CPU PSI<\/span><strong>\{cpuPressureSomeText\}<\/strong>/);
	assert.doesNotMatch(ioDetail, /\{loadPreviewText\}/, 'expanded facts must not reuse the collapsed preview sentence');
});
