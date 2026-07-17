// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./ServerCard.svelte', import.meta.url), 'utf8');

test('ServerCard renders advisory hold chips, wires the dense NoteForm props, and keeps only concise composer guidance', () => {
	assert.match(source, /note\.kind === 'hold'/);
	assert.match(source, /note\.gpu_indices/);
	assert.match(source, /monitor-note-item__kind">HOLD<\/span>/);
	assert.match(
		source,
		/<NoteForm[\s\S]*serverId=\{server\.server_id\}[\s\S]*gpus=\{server\.gpus\}[\s\S]*serverStatus=\{server\.status\}[\s\S]*lastSeen=\{server\.last_seen\}[\s\S]*active=\{notesExpanded\}[\s\S]*onCreated=\{onNoteCreated\}/
	);
	const composerHead = source.match(/monitor-card__memo-group--composer[\s\S]*?<div class="monitor-card__memo-group-head">[\s\S]*?<\/div>/)?.[0] ?? '';
	assert.match(composerHead, /monitor-card__memo-group-title">작성<\/span>/);
	assert.match(composerHead, /GPU 선택 시 작업 공유/);
	assert.doesNotMatch(composerHead, /예약 보장 아님|보장하지 않습니다/);
});

test('ServerCard derives active unexpired hold notes per GPU and passes cues into GpuBar', () => {
	assert.match(source, /activeHoldNotesByGpu\s*=\s*\$derived\.by/, 'missing derived per-GPU hold cue map');
	assert.match(source, /const holds:\s*Record<number,\s*GpuHoldCue\[]> = \{\};/, 'per-GPU advisory map should stay scoped to each card');
	assert.match(source, /note\.kind\s*===\s*'hold'/, 'hold cue map should only use hold notes');
	assert.match(source, /noteVisible\(note\)/, 'hold cue map should ignore expired notes');
	assert.match(source, /holdGpuIndices\(note\)/, 'hold cue map should validate GPU indices');
	assert.match(source, /note,\s*remaining:\s*noteRemainingText\(note\)/, 'GpuBar tooltip entries should keep relative expiry text');
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

test('ServerCard renders collapsed System as one resource-leading CPU RAM Storage line with normalized load labels only', () => {
	const preview = source.match(/<span class="monitor-card__footer-preview monitor-card__system-preview">[\s\S]*?monitor-card__footer-disclosure/)?.[0] ?? '';
	const cpuIndex = preview.indexOf('CPU {cpuPreviewText}');
	const ramIndex = preview.indexOf('RAM {ramPreviewText}');
	const storageIndex = preview.indexOf('Storage {storagePreviewText}');
	const loadIndex = preview.indexOf('monitor-card__load-preview');

	assert.ok(cpuIndex >= 0 && cpuIndex < ramIndex && ramIndex < storageIndex && storageIndex < loadIndex, 'CPU, RAM and Storage must remain visible before load context');
	assert.doesNotMatch(preview, /monitor-card__load-gauge/, 'collapsed summary should not spend width on a load gauge');
	assert.match(preview, /monitor-card__load-text">\{loadPreviewText\}<\/span>/, 'collapsed system preview should retain compact load text');
	assert.match(preview, /monitor-card__load-preview" title=\{loadAverageHelpText\}/, 'collapsed load context should keep the explanatory tooltip');
	assert.match(source, /aria-describedby=\{`system-load-help-\$\{server\.server_id\}`\}/, 'system disclosure should expose load help to keyboard and assistive technology users');
	assert.match(preview, /\{\/if\}\s*<span id=\{`system-load-help-\$\{server\.server_id\}`\} class="monitor-card__sr-only">\{loadAverageHelpText\}<\/span>/, 'load help target must remain mounted after the collapsed-only preview closes');
	for (const label of ['부하 여유', '부하 보통', '부하 높음', '부하 –']) assert.ok(source.includes(label), `missing collapsed load label: ${label}`);
	assert.doesNotMatch(source, /loadPreviewPrefixText/);
	assert.doesNotMatch(source, /return `\$\{loadPreviewPrefixText\}부하 \$\{loadText\} · CPU \$\{cpuText\}`;/, 'collapsed preview must not expose raw load or CPU count');
	assert.doesNotMatch(source, /마지막 ·/, 'historical or offline preview should stay neutral');
	assert.doesNotMatch(preview, /#each loadPreviewCauses as cause/, 'collapsed system preview should not append separate pressure causes');
	assert.doesNotMatch(preview, /· \{cause\.label\}/, 'collapsed system preview should not append cause chips');
	assert.match(source, /const ramPreviewText = \$derived\([\s\S]*?`\$\{ramPct\.toFixed\(0\)\}%`/, 'collapsed RAM preview should use percentage only');
	assert.match(source, /const storagePreviewText = \$derived\([\s\S]*?`\$\{storagePct\.toFixed\(0\)\}%`/, 'collapsed Storage preview should use percentage only');
	assert.doesNotMatch(preview, /monitor-card__system-preview-segment/, 'old segmented preview pills should be removed');
	assert.doesNotMatch(source, /monitor-card__system-preview-item/, 'old 4-tile micro-grid should be removed');
});

test('expanded System follows pressure capacity bottleneck detail hierarchy', () => {
	assert.match(source, /monitor-card__resource-overview/, 'expanded system should begin with a compact resource overview');
	assert.match(source, /class="monitor-card__resource-overview" role="group" aria-label="호스트 리소스"/, 'resource facts should expose an accessible group name');
	assert.match(source, /monitor-card__resource-item/, 'CPU RAM and Storage should share one aligned hierarchy');
	assert.match(source, /monitor-card__resource-value/, 'resource utilization should be the primary value');
	assert.match(source, /monitor-card__resource-meta/, 'capacity and CPU count should be secondary metadata');
	assert.match(source, /monitor-card__pressure-table/, 'load and PSI facts should form one diagnostic table');
	assert.match(source, /monitor-card__pressure-row/, 'diagnostic facts should use aligned rows instead of a flat token dump');
	assert.match(source, /ioPressureHelpText/, 'expanded system should explain stall pressure');
	assert.match(source, /loadDetailText/, 'expanded system should surface 1\/5\/15 load detail text');
	assert.match(source, /cpuCountText/, 'expanded system should surface logical CPU count');
	assert.match(source, /cpuRunningText/, 'expanded system should surface runnable task count');
	assert.match(source, /ramSystemDetailText/, 'expanded system should surface RAM utilization and capacity');
	assert.match(source, /storageSystemDetailText/, 'expanded system should surface Storage utilization and capacity');
	assert.match(source, /cpuPressureSomeText/, 'expanded system should surface CPU PSI some avg10');
	assert.match(source, /ioSomeText/, 'expanded system should surface I\/O PSI some avg10');
	assert.match(source, /ioFullText/, 'expanded system should surface I\/O PSI full avg10');
	assert.match(source, /ioBlockedText/, 'expanded system should surface blocked task count');
	assert.match(source, />실행 중</, 'procs_running must not be mislabeled as queued work');
	assert.match(source, />병목 단서</, 'PSI should be presented as an actionable bottleneck clue');

	const storageIndex = source.indexOf('<span class="monitor-card__subheading">Storage</span>');
	const hardwareIndex = source.indexOf('<div class="monitor-card__subheading">GPU 하드웨어</div>');
	assert.ok(storageIndex >= 0 && hardwareIndex > storageIndex, 'Storage detail should precede secondary GPU hardware facts');
	assert.doesNotMatch(source, /monitor-card__system-facts/, 'flat six-fact dump should be removed');
	assert.doesNotMatch(source, /monitor-card__system-summary/, 'old summary tile grid should stay removed');
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
	assert.doesNotMatch(source, /monitor-note-item__user">@\{resolveDisplayName\(note\)\}<\/span>/, 'display_name must not replace username ownership or delete identity');
});

test('ServerCard separates memo history from the composer and provides a deliberate empty state', () => {
	assert.match(source, /monitor-card__memo-group monitor-card__memo-group--history/);
	assert.match(source, /monitor-card__memo-group monitor-card__memo-group--composer/);
	assert.match(source, /monitor-card__memo-group-title">기록<\/span>/);
	assert.match(source, /monitor-card__memo-group-title">작성<\/span>/);
	assert.match(source, /GPU 선택 시 작업 공유/);
	assert.doesNotMatch(source, /GPU 선택 시 작업 공유[\s\S]*예약 보장 아님/);
	assert.match(source, /monitor-card__memo-empty/);
});

test('ServerCard exposes a non-text availability nudge without reordering cards', () => {
	assert.ok(source.includes('const availableGpuCount = $derived.by'));
	assert.match(source, /getCompactGpuState\(server\.status, server\.last_seen, gpu\)[\s\S]*=== 'available'/);
	assert.match(source, /operationalState === 'impaired' \? staleAvailabilityState/, 'impaired availability should not advertise free GPUs');
	assert.ok(source.includes('const hasAvailableGpu = $derived(availableGpuCount > 0)'));
	assert.ok(source.includes("data-has-available={hasAvailableGpu ? 'true' : 'false'}"));
});


test('ServerCard keeps expanded pressure classification while removing obsolete collapsed cause derivations', () => {
	assert.match(source, /const cpuPressureLevel = \$derived\(classifyPressure\(cpuPressureSome\)\)/);
	assert.match(source, /const ioPressureLevel = \$derived\.by/, 'I\/O pressure should still be derived from PSI and blocked-task telemetry');
	assert.doesNotMatch(source, /pressureLabel,/, 'pressureLabel import should be removed when collapsed cause copy is gone');
	assert.doesNotMatch(source, /const cpuPressureLabelText/);
	assert.doesNotMatch(source, /const ioPressureLabelText/);
	assert.doesNotMatch(source, /const cpuPressureCauseLabel/);
	assert.doesNotMatch(source, /const ioPressureCauseLabel/);
	assert.doesNotMatch(source, /const loadPreviewCauses/);
	assert.doesNotMatch(source, /#each loadPreviewCauses as cause/);
	assert.doesNotMatch(source, /CPU 여유|I\/O 여유/, 'collapsed preview must not render healthy cause labels');
});

test('ServerCard removes throughput fallback and any MB/s copy from the System UI', () => {
	assert.doesNotMatch(source, /formatDiskThroughput/, 'System UI must not keep an MB\/s formatter');
	assert.doesNotMatch(source, /diskReadBytesPerSecond|diskWriteBytesPerSecond|diskThroughputBytesPerSecond/, 'System UI must not derive disk throughput preview values');
	assert.doesNotMatch(source, /MB\/s/, 'System UI copy must not render MB\/s anywhere');
});

test('expanded System pressure table shows CPU and I/O PSI facts without R/W throughput cells', () => {
	assert.match(source, /monitor-card__pressure-table/, 'expanded pressure details should keep stable tabular formatting');
	assert.match(source, /<small>CPU PSI<\/small><strong>\{cpuPressureSomeText\}<\/strong>/, 'expanded details should show CPU PSI some');
	assert.match(source, /<small>I\/O some<\/small><strong>\{ioSomeText\}<\/strong>/, 'expanded details should show I\/O PSI some');
	assert.match(source, /<small>I\/O full<\/small><strong>\{ioFullText\}<\/strong>/, 'expanded details should show I\/O PSI full');
	assert.match(source, /<small>blocked<\/small><strong>\{ioBlockedText\}<\/strong>/, 'expanded details should show blocked tasks');
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

test('historical and offline System preview keeps a neutral qualitative load cue', () => {
	assert.match(source, /const systemPreviewUnavailableText = '–';/);
	assert.match(source, /const loadPreviewText = \$derived\(collapsedLoadLabel\(loadLevel\)\)/, 'collapsed preview should derive a compact qualitative load cue');
	assert.match(source, /\{#if isHistoricalSystemTelemetry && server\.system\}[\s\S]*monitor-card__last-sample-label[\s\S]*마지막 수집값[\s\S]*\{\/if\}/);
	assert.doesNotMatch(source, /마지막 ·/, 'historical collapsed preview should stay neutral');
	assert.match(source, /부하 –/, 'neutral fallback should use the qualitative unavailable load label');
});

test('expanded historical System keeps raw last-sample resource and pressure values', () => {
	const overview = source.match(/<div class="monitor-card__resource-overview"[\s\S]*?<\/div>/)?.[0] ?? '';
	const pressure = source.match(/<div class="monitor-card__pressure-table"[\s\S]*?<\/div>/)?.[0] ?? '';
	assert.match(source, /const loadDetailText = \$derived/);
	assert.match(source, /const cpuSystemDetailText = \$derived\(server\.system \? `\$\{cpuPct\.toFixed\(0\)\}%` : systemPreviewUnavailableText\)/);
	assert.match(source, /const cpuRunningText = \$derived/);
	assert.match(source, /const cpuPressureSomeText = \$derived/);
	assert.match(overview, /<small>CPU<\/small>[\s\S]*<strong class="monitor-card__resource-value">\{cpuSystemDetailText\}<\/strong>/);
	assert.match(overview, /<small>RAM<\/small>[\s\S]*<strong class="monitor-card__resource-value">\{ramSystemDetailText\}<\/strong>/);
	assert.match(overview, /<small>Storage<\/small>[\s\S]*<strong class="monitor-card__resource-value">\{storageSystemDetailText\}<\/strong>/);
	assert.match(source, /<small>CPU PSI<\/small><strong>\{cpuPressureSomeText\}<\/strong>/);
	assert.doesNotMatch(pressure, /\{loadPreviewText\}/, 'expanded facts must not reuse the collapsed preview sentence');
});
