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
		/state=\{getCompactGpuState\(server\.status, server\.last_seen, gpu\)\}/,
		'GPU row should receive the shared availability state'
	);
	assert.match(
		gpuBar,
		/advisoryHolds=\{activeHoldNotesByGpu\[gpu\.index\]\s*\?\?\s*\[\]\}/,
		'each GPU row should receive only its own hold cues'
	);
});

test('ServerCard header collapses identity into one dense baseline with inline host and freshness', () => {
	const titleRow = source.match(/<div class="monitor-card__title-row">[\s\S]*?\{#if statusReasonText/)?.[0] ?? '';
	assert.match(titleRow, /monitor-card__title-line/, 'title row should keep one inline identity line');
	assert.match(titleRow, /monitor-card__status/, 'title row should keep the accessible health status');
	assert.match(titleRow, /monitor-card__host/, 'host should live in the inline identity line');
	assert.match(titleRow, /monitor-card__refresh/, 'freshness should live in the inline identity line');
	assert.match(titleRow, /monitor-card__edit-button/, 'edit control should stay in the compact header row');
	assert.doesNotMatch(source, /monitor-card__meta/, 'separate meta row should be removed');
	assert.doesNotMatch(source, /monitor-card__network/, 'decorative network chip should not remain in the header');
	assert.match(source, /const endpointText = \$derived/, 'visible endpoint should be derived once');
	assert.match(source, /server\.port !== DEFAULT_SSH_PORT/, 'non-default SSH ports must remain visible');
	assert.match(source, /\{endpointText\}/, 'the compact header should render the operational endpoint');
	assert.match(source, /freshnessIssueText\(server\.last_seen, noteNowMs\)/, 'freshness should be derived as exception-only copy');
	assert.match(source, /FRESHNESS_WARNING_AFTER_MS/, 'healthy sub-threshold updates must not emit second-by-second copy');
	assert.match(titleRow, /\{#if refreshText\}[\s\S]*monitor-card__refresh[\s\S]*\{\/if\}/, 'freshness markup should exist only for stale telemetry');
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

test('ServerCard renders collapsed system preview as one inline baseline with explicit I/O PSI', () => {
	const preview = source.match(/<span class="monitor-card__footer-preview monitor-card__system-preview">[\s\S]*?<\/span>\s*\{\/if\}/)?.[0] ?? '';
	assert.match(preview, /monitor-card__system-preview-segment/, 'collapsed system preview should use inline segments');
	assert.match(preview, /monitor-card__system-preview-label">CPU<\/span>/);
	assert.match(preview, /monitor-card__system-preview-label">RAM<\/span>/);
	assert.match(preview, /monitor-card__system-preview-label">I\/O<\/span>/);
	assert.match(preview, /monitor-card__system-preview-label">Disk<\/span>/);
	assert.match(source, /ioPreviewText/, 'collapsed preview should derive an explicit I/O PSI value');
	assert.doesNotMatch(source, /monitor-card__system-preview-item/, 'old 4-tile micro-grid should be removed');
	assert.doesNotMatch(preview, /<small>CPU<\/small>/, 'old two-row label/value tiles should be removed');
});

test('expanded System keeps a dense expert I/O detail row with stall-pressure help', () => {
	assert.match(source, /monitor-card__io-detail/, 'expanded system should expose a dedicated dense I\/O detail row');
	assert.match(source, /monitor-card__io-detail-copy/, 'expanded system should include concise explanatory microcopy');
	assert.match(source, /monitor-card__io-detail-metrics/, 'expanded system should keep the PSI metrics inline');
	assert.match(source, /ioPressureHelpText/, 'expanded system should provide a tooltip or title explaining stall pressure');
	assert.match(source, /ioSomeText/, 'expanded system should surface PSI some avg10');
	assert.match(source, /ioFullText/, 'expanded system should surface PSI full avg10');
	assert.match(source, /ioBlockedText/, 'expanded system should surface blocked task count');
	const facts = source.match(/<div class="monitor-card__system-facts">[\s\S]*?<\/div>/)?.[0] ?? '';
	assert.match(facts, /<small>RAM<\/small><strong>\{ramPercentText\}<\/strong>/, 'summary facts should use compact RAM percent instead of truncating capacity text');
	assert.doesNotMatch(source, /monitor-card__system-summary/, 'old summary tile grid should be removed from the dense system panel');
	assert.doesNotMatch(source, /monitor-card__summary-item/, 'old summary tiles should not remain');
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

test('note expiry helper speaks in explicit Korean relative phrases and expired state', () => {
	assert.match(source, /return '만료됨';/, 'expired notes should say 만료됨');
	assert.ok(source.includes('return `${minutes}분 남음`;'));
	assert.ok(source.includes('return `${hours}시간 남음`;'));
	assert.ok(source.includes('return `${days}일 남음`;'));
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
	assert.ok(source.includes("getCompactGpuState(server.status, server.last_seen, gpu) === 'available'"));
	assert.ok(source.includes('const hasAvailableGpu = $derived(availableGpuCount > 0)'));
	assert.ok(source.includes("data-has-available={hasAvailableGpu ? 'true' : 'false'}"));
});
