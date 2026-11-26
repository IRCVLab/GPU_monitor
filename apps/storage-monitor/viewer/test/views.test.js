import test from 'node:test';
import assert from 'node:assert/strict';
import * as overview from '../views/overview.js';
import {renderHeader} from '../views/header.js';
import {drawUsers} from '../views/users.js';
import {parseUrl} from '../router.js';
import {normalizeDetail,applyObservedCapacity} from '../api.js';
import {documentFixture} from './dom.js';
function textTree(node) {
  let out = node.textContent || '';
  if (node.innerHTML) out += node.innerHTML;
  for (const child of node.children || []) out += textTree(child);
  return out;
}
function findAll(node, predicate, out = []) {
  if (node && predicate(node)) out.push(node);
  for (const child of (node && node.children) || []) findAll(child, predicate, out);
  return out;
}
function findByClass(node, className) {
  return findAll(node, el => (el.className || '').split(/\s+/).includes(className));
}
function hangulString(text) {
  return /[가-힣]/.test(text || '');
}
function testOverviewRenderingKeepsStableOrderAndVisibleCapacityBars() {
  const document = documentFixture();
  assert.strictEqual(typeof overview.buildOverviewServer, 'function', 'overview row builder must be exposed');
  assert.strictEqual(typeof overview.renderOverviewList, 'function', 'overview renderer must be exposed');

  const failedRow = overview.buildOverviewServer(
    {
      id: 'beta-2',
      display_name: 'beta',
      order: 20,
      mount_count: 2,
      snapshot_availability: 'available',
      freshness: 'fresh',
      latest_pull_status: 'succeeded',
      latest_scan_result: 'failed',
      configuration_sync: 'in_sync',
      active_job: { id: 'job-1', server_id: 'beta-2', kind: 'rescan', state: 'running', actor: 'operator-1', requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null },
    },
    {
      server_id: 'beta-2',
      mounts: [
        { path: '/data', df_total: 1000 * 1024 ** 3, df_used: 950 * 1024 ** 3, df_use_pct: 95, used_pct: 95, severity: "critical", df_avail: 800 * 1024 ** 3 },
        { path: '/archive', df_total: 5 * 1024 ** 4, df_used: 3 * 1024 ** 4, df_use_pct: 60, used_pct: 60, severity: "normal", df_avail: 2 * 1024 ** 4 },
      ],
    },
  );
  const staleRow = overview.buildOverviewServer(
    {
      id: 'alpha-1',
      display_name: 'alpha',
      order: 10,
      mount_count: 1,
      snapshot_availability: 'available',
      freshness: 'stale',
      latest_pull_status: 'succeeded',
      latest_scan_result: 'complete',
      configuration_sync: 'in_sync',
      active_job: null,
    },
    {
      server_id: 'alpha-1',
      mounts: [{ path: '/scratch', df_total: 1000 * 1024 ** 3, df_used: 810 * 1024 ** 3, df_use_pct: 81, used_pct: 81, severity: "warning", df_avail: 700 * 1024 ** 3 }],
    },
  );

  const overviewList = document.getElementById('overviewList');
  const opened = [];
  overview.renderOverviewList(overviewList, [failedRow, staleRow], { onOpenServer: (serverId) => opened.push(serverId) });

  assert.strictEqual(overviewList.children.length, 2, 'all servers must render into one dense list');
  assert.strictEqual(overviewList.children[0].tagName, 'LI', 'overview list items must use native li semantics');
  assert.strictEqual(overviewList.children[0].children[0].tagName, 'A', 'each overview li must contain a native monitor-card link');
  assert.strictEqual(overviewList.children[0].children[0].dataset.serverId, 'beta-2', 'inventory order must not change because of severity');
  assert.strictEqual(overviewList.children[1].children[0].dataset.serverId, 'alpha-1', 'inventory order must remain stable for later rows');

  const failedButton = overviewList.children[0].children[0];
  const failedText = textTree(failedButton);
  assert(failedText.includes('beta'), 'row must contain the server display name');
  assert.strictEqual(failedButton.getAttribute('data-primary-status'), failedRow.primaryStatus.code, 'rendered primary status code must match the overview model');
  const primaryState = findByClass(failedButton, 'overview-status-text')[0];
  assert(primaryState, 'primary exceptional state must remain visible in the compact card header');
  assert(hangulString(textTree(primaryState)), 'primary exceptional state must expose visible Korean text');
  assert.strictEqual(findByClass(failedButton, 'overview-status-dot').length, 1, 'primary exceptional state must include a compact shape cue');
  assert(failedText.includes('95%'), 'capacity bars must still show percentage text');
  assert(failedText.includes('800 GB'), 'capacity bars must still show available-byte text');

  const staleButton = overviewList.children[1].children[0];
  assert.strictEqual(staleButton.getAttribute('data-primary-status'), staleRow.primaryStatus.code, 'rendered stale status code must match the overview model');
  assert(hangulString(textTree(findByClass(staleButton, 'overview-status-text')[0])), 'stale state must expose visible Korean text');
  assert(textTree(staleButton).includes('81%'), 'warning-capacity rows must still expose their compact bar text');

  assert.equal(parseUrl(failedButton.getAttribute('href')).serverId,'beta-2');
}
test('testOverviewRenderingKeepsStableOrderAndVisibleCapacityBars',testOverviewRenderingKeepsStableOrderAndVisibleCapacityBars);
function testSnapshotLoadFailureRendersAsVisibleException() {
  const document = documentFixture();
  const row = overview.buildOverviewServer(
    {
      id: 'gamma-3',
      display_name: 'gamma',
      order: 30,
      mount_count: 3,
      snapshot_availability: 'available',
      freshness: 'fresh',
      latest_pull_status: 'succeeded',
      latest_scan_result: 'complete',
      configuration_sync: 'in_sync',
      active_job: null,
    },
    null,
    new Error('network failed'),
  );
  assert.strictEqual(row.primaryStatus.code, 'snapshot_load_failed', 'healthy summaries with client snapshot failures must expose a dedicated exceptional status');
  const overviewList = document.getElementById('overviewList');
  overview.renderOverviewList(overviewList, [row], { onOpenServer() {} });
  const button = overviewList.children[0].children[0];
  assert.strictEqual(button.getAttribute('data-primary-status'), 'snapshot_load_failed');
  const state = findByClass(button, 'overview-status-text')[0];
  assert(state, 'snapshot load failures must render a visible compact state');
  assert(hangulString(textTree(state)), 'snapshot load failure state must render Korean text');
  assert.strictEqual(findByClass(button, 'overview-status-dot').length, 1, 'snapshot load failure state must render a visible shape cue');
}
test('testSnapshotLoadFailureRendersAsVisibleException',testSnapshotLoadFailureRendersAsVisibleException);
function testMountCentricOverviewDomFieldsAndStableNavigation() {
  const document = documentFixture();
  const row = overview.buildOverviewServer(
    { id: 'hinton', display_name: 'hinton', order: 99, mount_count: 2 },
    {
      server_id: 'hinton',
      selected_roots: [
        { mount_id: 'home', capacity_id: 'dev-8-1', storage_media: 'ssd' },
        { mount_id: 'data', capacity_id: 'dev-8-16', block_media: 'hdd' },
      ],
      mounts: [
        { mount_id: 'home', path: '/home', df_total: 1000 * 1024 ** 3, df_used: 400 * 1024 ** 3, df_avail: 600 * 1024 ** 3, df_use_pct: 40, used_pct: 40, severity: "normal" },
        { mount_id: 'data', path: '/data', df_total: 2000 * 1024 ** 3, df_used: 1800 * 1024 ** 3, df_avail: 200 * 1024 ** 3, df_use_pct: 90, used_pct: 90, severity: "warning" },
      ],
    },
  );
  const later = overview.buildOverviewServer(
    { id: 'atlas', display_name: 'atlas', order: 1, mount_count: 1 },
    {
      server_id: 'atlas',
      selected_roots: [{ mount_id: 'archive', capacity_id: 'dev-9-1', storage_media: 'mixed' }],
      mounts: [{ mount_id: 'archive', path: '/archive', df_total: 3000 * 1024 ** 3, df_used: 300 * 1024 ** 3, df_avail: 2700 * 1024 ** 3, df_use_pct: 10, used_pct: 10, severity: "normal" }],
    },
  );
  const opened = [];
  const list = document.getElementById('overviewList');
  overview.renderOverviewList(list, [row, later], { onOpenServer: serverId => opened.push(serverId) });

  assert.deepStrictEqual(list.children.map(item => item.children[0].dataset.serverId), ['hinton', 'atlas'], 'rendering must not sort by order, name, capacity, or status');
  const hintonButton = list.children[0].children[0];
  assert.strictEqual(hintonButton.getAttribute('aria-label'), undefined, 'row button must not mask descendant subtotal and mount text with a short aria-label');
  assert.strictEqual(findByClass(hintonButton, 'overview-server-subtotal').length, 0, 'compact server rows must not render subtotal labels');
  assert.strictEqual(findByClass(hintonButton, 'overview-quiet').length, 0, 'normal server rows must not render a normal label');
  const accessibleRowText = textTree(hintonButton);
  assert(accessibleRowText.includes('hinton'), 'row descendant text must include the visible server label');
  const cells = findByClass(hintonButton, 'overview-mount');
  assert.strictEqual(cells.length, 2, 'mount-centric overview must render one compact metric row per mount');
  assert.deepStrictEqual(cells.map(cell => findByClass(cell, 'overview-mount-path')[0].textContent), ['/home', '/data'], 'mount metric rows must preserve snapshot mount order exactly');
  const first = cells[0];
  const fieldClasses = first.children.map(child => child.className);
  assert.deepStrictEqual(fieldClasses, [
    'overview-media-label',
    'overview-mount-body',
  ], 'each mount row must use a compact media identity plus one aligned metric body');
  assert.strictEqual(findByClass(first, 'overview-media-label')[0].textContent, 'SSD', 'media labels must use neutral storage-class text');
  assert.strictEqual(findByClass(first, 'overview-mount-used-total').length, 0, 'compact mount strip must not render used/total capacity text');
  assert.strictEqual(findByClass(first, 'overview-mount-pct')[0].textContent, '40%', 'utilization percent must be rendered as its own compact field');
  assert.strictEqual(textTree(findByClass(first, 'overview-mount-capacity')[0]), '볼륨 총 1000 GB · 여유 600 GB', 'mount rows must expose the filesystem total capacity beside remaining capacity');
  assert(accessibleRowText.includes('/home') && accessibleRowText.includes('SSD') && accessibleRowText.includes('40%') && accessibleRowText.includes('총 1000 GB') && accessibleRowText.includes('여유 600 GB'), 'mount path, media, percent, total, and free information must remain accessible as descendant text');
  assert(!accessibleRowText.includes('400 GB / 1000 GB'), 'compact mount strip must omit used/total text');

  assert.equal(parseUrl(hintonButton.getAttribute('href')).serverId,'hinton');
}
test('testMountCentricOverviewDomFieldsAndStableNavigation',testMountCentricOverviewDomFieldsAndStableNavigation);
function testMountStatusTextAppearsOnlyForExceptionalPressure() {
  const document = documentFixture();
  const row = overview.buildOverviewServer(
    { id: 'pressure-1', display_name: 'pressure', mount_count: 3, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    {
      server_id: 'pressure-1',
      selected_roots: [
        { mount_id: 'healthy', capacity_id: 'dev-8-1', storage_media: 'ssd' },
        { mount_id: 'warn', capacity_id: 'dev-8-2', storage_media: 'ssd' },
        { mount_id: 'crit', capacity_id: 'dev-8-3', storage_media: 'hdd' },
      ],
      mounts: [
        { mount_id: 'healthy', path: '/healthy', df_total: 1000 * 1024 ** 3, df_used: 400 * 1024 ** 3, df_avail: 600 * 1024 ** 3, df_use_pct: 40, used_pct: 40, severity: "normal" },
        { mount_id: 'warn', path: '/warn', df_total: 1000 * 1024 ** 3, df_used: 810 * 1024 ** 3, df_avail: 190 * 1024 ** 3, df_use_pct: 81, used_pct: 81, severity: "warning" },
        { mount_id: 'crit', path: '/crit', df_total: 1000 * 1024 ** 3, df_used: 930 * 1024 ** 3, df_avail: 70 * 1024 ** 3, df_use_pct: 93, used_pct: 93, severity: "critical" },
      ],
    },
  );
  const list = document.getElementById('overviewList');
  overview.renderOverviewList(list, [row], { onOpenServer() {} });
  const cells = findByClass(list.children[0].children[0], 'overview-mount');
  const capacityTexts = cells.map(cell => textTree(findByClass(cell, 'overview-mount-capacity')[0]));
  assert.strictEqual(capacityTexts[0], '볼륨 총 1000 GB · 여유 600 GB', 'healthy mount capacity text must remain compact and complete');
  assert.strictEqual(capacityTexts[1], '볼륨 총 1000 GB · 여유 190 GB', 'warning mount capacity text must remain compact and complete');
  assert.strictEqual(capacityTexts[2], '볼륨 총 1000 GB · 여유 70.0 GB', 'critical mount capacity text must remain compact and complete');
  const footerText = textTree(findByClass(list.children[0].children[0], 'overview-card-footer')[0]);
  assert(footerText.includes('주의 1') && footerText.includes('위험 1'), 'non-color pressure labels must be consolidated in the card footer');
  assert(findByClass(list.children[0].children[0], 'overview-mount')[1].getAttribute('aria-label').includes('주의'), 'warning mount must expose its exact state without relying on color');
  assert(findByClass(list.children[0].children[0], 'overview-mount')[2].getAttribute('aria-label').includes('위험'), 'critical mount must expose its exact state without relying on color');
}
test('testMountStatusTextAppearsOnlyForExceptionalPressure',testMountStatusTextAppearsOnlyForExceptionalPressure);
function testServerHeaderMetaIsActionableMountCountOnly() {
  const document = documentFixture();
  const list = document.getElementById('overviewList');
  const row = overview.buildOverviewServer(
    { id: 'alpha-1', display_name: 'alpha', mount_count: 2 },
    {
      server_id: 'alpha-1',
      selected_roots: [
        { mount_id: 'data', capacity_id: 'dev-8-16', storage_media: 'hdd' },
        { mount_id: 'data-bind', capacity_id: 'dev-8-16', storage_media: 'hdd' },
      ],
      mounts: [
        { mount_id: 'data', path: '/data', df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
        { mount_id: 'data-bind', path: '/data-bind', df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
      ],
    },
  );
  assert.strictEqual(row.aggregate.availableLabel, '500 B', 'model may keep aggregate capacity for non-header calculations');
  overview.renderOverviewList(list, [row], { onOpenServer() {} });
  const button = list.children[0].children[0];
  const metaText = textTree(findByClass(button, 'overview-meta')[0]);
  assert.strictEqual(metaText, '2개 마운트', 'server header metadata must be actionable mount count only');
  assert(!metaText.includes(' free'), 'server header metadata must not include total available/free aggregate text');
  assert(!metaText.includes('500 B'), 'server header metadata must not expose aggregate capacity');
  assert.strictEqual(findByClass(button, 'overview-server-subtotal').length, 0, 'compact server rows must not render subtotal labels');
}
test('testServerHeaderMetaIsActionableMountCountOnly',testServerHeaderMetaIsActionableMountCountOnly);
function testSharedCapacityOverviewGroupsPhysicalDiskAndPreservesScanPathOrder() {
  const document = documentFixture();
  const row = overview.buildOverviewServer(
    {
      id: 'neo',
      display_name: 'neo',
      mount_count: 2,
      snapshot_availability: 'available',
      freshness: 'fresh',
      latest_pull_status: 'succeeded',
      latest_scan_result: 'complete',
      configuration_sync: 'in_sync',
      active_job: null,
    },
    {
      server_id: 'neo',
      selected_roots: [
        { mount_id: 'home', capacity_id: 'dev-259-3', mountpoint: '/', scan_root: '/home', storage_media: 'ssd', scanned_bytes: 700 * 1024 ** 3 },
        { mount_id: 'data', capacity_id: 'dev-259-3', mountpoint: '/', scan_root: '/data', storage_media: 'ssd', scanned_bytes: 20 * 1024 },
      ],
      mounts: [
        { mount_id: 'home', path: '/home', mountpoint: '/', scan_root: '/home', df_total: 3600 * 1024 ** 3, df_used: 2780 * 1024 ** 3, df_avail: 820 * 1024 ** 3, df_use_pct: 77, used_pct: 77, severity: "normal", scanned_bytes: 700 * 1024 ** 3 },
        { mount_id: 'data', path: '/data', mountpoint: '/', scan_root: '/data', df_total: 3600 * 1024 ** 3, df_used: 2780 * 1024 ** 3 + 8192, df_avail: 820 * 1024 ** 3 - 8192, df_use_pct: 77, used_pct: 77, severity: "normal", scanned_bytes: 20 * 1024 },
      ],
    },
  );
  const list = document.getElementById('overviewList');
  overview.renderOverviewList(list, [row], { onOpenServer() {} });

  const card = list.children[0].children[0];
  assert.strictEqual(findByClass(card, 'overview-mount').length, 1, 'shared-capacity scan roots must render one physical-capacity summary');
  assert.strictEqual(findByClass(card, 'overview-pressure-bar').length, 1, 'shared-capacity groups must keep one shared utilization bar');
  const capacityLabel = findByClass(card, 'overview-capacity-label')[0];
  assert.strictEqual(row.aggregate.totalBytes, 3600 * 1024 ** 3, 'writes between scan roots must not remove the volume from the aggregate');
  const scanPaths = findByClass(card, 'overview-scan-paths')[0];
  assert(capacityLabel, 'multi-root capacity groups must expose a distinct physical-capacity label');
  assert(scanPaths, 'multi-root capacity groups must expose a distinct scan-root line');
  assert.strictEqual(textTree(capacityLabel), '볼륨 /', 'shared folders must show their actual volume mount point');
  assert.deepStrictEqual(scanPaths.children.map(folder => folder.children.map(cell => cell.textContent)), [['/home', '700 GB'], ['/data', '20.0 KB']]);
  assert.strictEqual(textTree(findByClass(card, 'overview-meta')[0]), '2개 마운트', 'server header must still report actionable scan-root count');
  assert(textTree(card).includes('77%') && textTree(card).includes('볼륨 총 3.52 TB · 여유 820 GB'), 'shared-capacity overview must render one total for the shared physical capacity');
}
test('testSharedCapacityOverviewGroupsPhysicalDiskAndPreservesScanPathOrder',testSharedCapacityOverviewGroupsPhysicalDiskAndPreservesScanPathOrder);
function testOverviewSharedCapacityRequiresKnownConsistentMetrics() {
  const document = documentFixture();
  const row = overview.buildOverviewServer(
    { id: 'neo', display_name: 'neo', mount_count: 4 },
    {
      server_id: 'neo',
      selected_roots: [
        { mount_id: 'unknown-home', capacity_id: 'dev-8-1', scan_root: '/unknown-home' },
        { mount_id: 'unknown-data', capacity_id: 'dev-8-1', scan_root: '/unknown-data' },
        { mount_id: 'mismatch-home', capacity_id: 'dev-8-2', scan_root: '/mismatch-home' },
        { mount_id: 'mismatch-data', capacity_id: 'dev-8-2', scan_root: '/mismatch-data' },
        { mount_id: 'missing-pct-home', capacity_id: 'dev-8-3', scan_root: '/missing-pct-home' },
        { mount_id: 'missing-pct-data', capacity_id: 'dev-8-3', scan_root: '/missing-pct-data' },
        { mount_id: 'mismatch-avail-home', capacity_id: 'dev-8-4', scan_root: '/mismatch-avail-home' },
        { mount_id: 'mismatch-avail-data', capacity_id: 'dev-8-4', scan_root: '/mismatch-avail-data' },
      ],
      mounts: [
        { mount_id: 'unknown-home', path: '/unknown-home', df_total: 1000, df_used: 400, df_use_pct: 40, used_pct: 40 },
        { mount_id: 'unknown-data', path: '/unknown-data', df_total: 1000, df_used: 400, df_use_pct: 40, used_pct: 40 },
        { mount_id: 'mismatch-home', path: '/mismatch-home', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40, used_pct: 40, severity: "critical" },
        { mount_id: 'mismatch-data', path: '/mismatch-data', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 41, used_pct: 41, severity: "critical" },
        { mount_id: 'missing-pct-home', path: '/missing-pct-home', df_total: 1000, df_used: 400, df_avail: 600 },
        { mount_id: 'missing-pct-data', path: '/missing-pct-data', df_total: 1000, df_used: 400, df_avail: 600 },
        { mount_id: 'mismatch-avail-home', path: '/mismatch-avail-home', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40, used_pct: 40, severity: "critical" },
        { mount_id: 'mismatch-avail-data', path: '/mismatch-avail-data', df_total: 1000, df_used: 400, df_avail: 500, df_use_pct: 40, used_pct: 40, severity: "critical" },
      ],
    },
  );

  assert.strictEqual(row.capacityGroups.length, 8, 'unknown or mismatched physical-capacity metrics must keep same-identity scan roots separate');
  assert.deepStrictEqual(Array.from(row.capacityGroups, group => group.scanRoots[0].path), [
    '/unknown-home',
    '/unknown-data',
    '/mismatch-home',
    '/mismatch-data',
    '/missing-pct-home',
    '/missing-pct-data',
    '/mismatch-avail-home',
    '/mismatch-avail-data',
  ], 'rejected capacity merges must preserve original scan-root order');
}
test('testOverviewSharedCapacityRequiresKnownConsistentMetrics',testOverviewSharedCapacityRequiresKnownConsistentMetrics);
function testUnknownMountCapacityDomStaysNeutralAndAccessible() {
  const document = documentFixture();
  const row = overview.buildOverviewServer(
    {
      id: 'unknown-1',
      display_name: 'unknown',
      mount_count: 2,
      snapshot_availability: 'available',
      freshness: 'fresh',
      latest_pull_status: 'succeeded',
      latest_scan_result: 'complete',
      configuration_sync: 'in_sync',
      active_job: null,
    },
    {
      server_id: 'unknown-1',
      selected_roots: [
        { mount_id: 'missing-free', capacity_id: 'dev-8-1', storage_media: 'unknown' },
        { mount_id: 'invalid-cap', capacity_id: 'dev-8-16', storage_media: 'mixed' },
      ],
      mounts: [
        { mount_id: 'missing-free', path: '/missing-free', df_total: 2048, df_used: 1024, df_use_pct: 95, used_pct: 95 },
        { mount_id: 'invalid-cap', path: '/invalid-cap', df_total: '4096', df_used: -1, df_avail: Infinity, df_use_pct: 80, used_pct: null },
      ],
    },
  );
  assert.strictEqual(row.primaryStatus.code, 'normal', 'unknown-only mount pressure must not promote the server row to warning or critical');
  const list = document.getElementById('overviewList');
  overview.renderOverviewList(list, [row], { onOpenServer() {} });
  const button = list.children[0].children[0];
  assert.strictEqual(button.getAttribute('data-primary-status'), 'normal', 'rendered primary status must remain normal for unknown-only pressure');
  const text = textTree(button);
  assert(text.includes('/missing-free') && text.includes('Unknown'), 'unknown media/path must remain visible and accessible');
  assert(text.includes('여유 미확인'), 'unknown free capacity must render honest Korean copy');
  assert(!text.includes('— / —'), 'compact mount strip must omit used/total capacity text');
  assert(!text.includes('여유 0 B'), 'unknown free capacity must never render as 0 B free');
  assert.strictEqual(button.getAttribute('aria-label'), undefined, 'unknown mount details must not be hidden behind a row aria-label');
}
test('testUnknownMountCapacityDomStaysNeutralAndAccessible',testUnknownMountCapacityDomStaysNeutralAndAccessible);
function testOverviewMonitorCardHierarchyContract() {
  const document = documentFixture();
  const list = document.getElementById('overviewList');
  const row = overview.buildOverviewServer(
    { id: 'dense-1', display_name: 'dense', mount_count: 3, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    {
      server_id: 'dense-1',
      selected_roots: [
        { mount_id: 'alpha', capacity_id: 'dev-8-1', storage_media: 'ssd' },
        { mount_id: 'beta', capacity_id: 'dev-8-2', storage_media: 'hdd' },
        { mount_id: 'gamma', capacity_id: 'dev-8-3', storage_media: 'mixed' },
      ],
      mounts: [
        { mount_id: 'alpha', path: '/alpha', df_total: 1000 * 1024 ** 3, df_used: 200 * 1024 ** 3, df_avail: 800 * 1024 ** 3, df_use_pct: 20, used_pct: 20, severity: "normal" },
        { mount_id: 'beta', path: '/beta', df_total: 1000 * 1024 ** 3, df_used: 850 * 1024 ** 3, df_avail: 150 * 1024 ** 3, df_use_pct: 85, used_pct: 85, severity: "warning" },
        { mount_id: 'gamma', path: '/gamma', df_total: 1000 * 1024 ** 3, df_used: 940 * 1024 ** 3, df_avail: 60 * 1024 ** 3, df_use_pct: 94, used_pct: 94, severity: "critical" },
      ],
    },
  );
  let opened = 0;
  overview.renderOverviewList(list, [row], { onOpenServer() { opened += 1; } });
  const card = list.children[0].children[0];
  const text = textTree(card);
  assert.strictEqual(card.tagName, 'A', 'each server must render as a native monitor-card link');
  assert.strictEqual(findByClass(card, 'overview-status-dot').length, 1, 'server state must use one compact status dot');
  assert.strictEqual(findByClass(card, 'overview-badge').length, 0, 'server cards must not lead with a large warning pill');
  assert.strictEqual(findByClass(card, 'overview-card-state').length, 0, 'overview must not render a separate healthy or exceptional state column');
  assert(!text.includes('전체') && !text.includes('집계'), 'overview rows must not include page-level or aggregate capacity copy');
  assert.strictEqual(findByClass(card, 'overview-card-header').length, 1, 'server identity must use the same compact card header anatomy as GPU Monitor');
  assert.strictEqual(findByClass(card, 'overview-card-title-row').length, 1, 'server identity must preserve the shared GPU Monitor title-row wrapper');
  assert.strictEqual(findByClass(card, 'overview-card-title-line').length, 1, 'server name, status, and metadata must remain in one shared title line');
  assert.strictEqual(findByClass(card, 'overview-card-status').length, 1, 'server status must use the shared inline GPU Monitor status wrapper');
  assert.strictEqual(findByClass(card, 'overview-status-text').length, 1, 'healthy status text must remain available to assistive technology');
  assert(findByClass(card, 'overview-status-text')[0].className.includes('overview-sr-only'), 'healthy status copy must stay visually hidden like GPU Monitor dev');
  assert.strictEqual(findByClass(card, 'overview-mounts').length, 1, 'overview mounts must use one compact metric-list body');
  assert.strictEqual(findByClass(card, 'overview-mount').length, 3, 'overview must keep every actionable mount');
  assert.strictEqual(findByClass(card, 'overview-card-footer').length, 1, 'server totals and pressure summary must live in one quiet card footer');
  const fields = findByClass(card, 'overview-mount').map(cell => cell.children.map(child => child.className));
  assert.deepStrictEqual(fields, [
    ['overview-media-label', 'overview-mount-body'],
    ['overview-media-label', 'overview-mount-body'],
    ['overview-media-label', 'overview-mount-body'],
  ], 'each mount row must use the GPU-row anatomy: compact identity chip plus one metric body');
  assert.deepStrictEqual(findByClass(card, 'overview-mount').map(cell => textTree(findByClass(cell, 'overview-mount-path')[0])), ['/alpha', '/beta', '/gamma'], 'monitor cards must preserve snapshot mount order');
  assert.strictEqual(findByClass(card, 'overview-pressure-bar').length, 3, 'every mount keeps a visible full-width usage graph');
  assert.equal(parseUrl(card.getAttribute('href')).tab,'treemap');
  const healthyRow = overview.buildOverviewServer(
    { id: 'healthy-1', display_name: 'healthy', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    {
      server_id: 'healthy-1',
      selected_roots: [{ mount_id: 'healthy-root', capacity_id: 'dev-9-1', storage_media: 'ssd' }],
      mounts: [{ mount_id: 'healthy-root', path: '/data', df_total: 1000 * 1024 ** 3, df_used: 200 * 1024 ** 3, df_avail: 800 * 1024 ** 3, df_use_pct: 20, used_pct: 20, severity: "normal" }],
    },
  );
  overview.renderOverviewList(list, [healthyRow], { onOpenServer() {} });
  const healthyStatusText = findByClass(list.children[0].children[0], 'overview-status-text')[0];
  assert.strictEqual(textTree(healthyStatusText), '정상', 'a truly healthy server must expose the same hidden Korean status label as GPU Monitor dev');
  assert(healthyStatusText.className.includes('overview-sr-only'), 'healthy status text must stay visually hidden');
}
test('testOverviewMonitorCardHierarchyContract',testOverviewMonitorCardHierarchyContract);
function testFleetUsageRanksByAccountNameAndReportsSnapshotCoverage() {
  const { buildFleetUserUsage } = overview;
  const usage = buildFleetUserUsage([
    { id: 'alpha', display_name: 'Alpha', freshness: 'fresh' },
    { id: 'beta', display_name: 'Beta', freshness: 'stale' },
    { id: 'missing', display_name: 'Missing' },
  ], [
    { id: 'alpha', snapshot: { users: [
      { uid: 1001, name: 'kim', bytes: 39 },
      { uid: 1002, name: 'lee', bytes: 33 },
      { uid: 1030, name: '1030', bytes: 90 },
      { uid: 0, name: 'root', bytes: 90 },
      { uid: 33, name: 'www-data', bytes: 100 },
    ] } },
    { id: 'beta', snapshot: { users: [
      { uid: 1002, name: 'kim', bytes: 12 },
      { uid: 1001, name: 'lee', bytes: 7 },
    ] } },
    { id: 'missing', snapshot: null },
  ]);
  assert.deepStrictEqual(usage.rows.map(row => [row.name, row.bytes]), [['root', 90], ['kim', 51], ['lee', 40]]);
  assert.deepStrictEqual([...usage.rows[1].servers.values()].map(row => [row.name, row.bytes]), [['Alpha', 39], ['Beta', 12]]);
  assert.deepStrictEqual([usage.scannedServers, usage.configuredServers, usage.staleServers], [2, 3, 1]);
}
test('testFleetUsageRanksByAccountNameAndReportsSnapshotCoverage',testFleetUsageRanksByAccountNameAndReportsSnapshotCoverage);
function testUsersChartTracksSizeAndFilteredMeasurements() {
  const elements = new Map();
  const element = () => ({
    style: {}, children: [], dataset: {}, textContent: "", innerHTML: "",
    appendChild(child) { this.children.push(child); },
  });
  const getElement = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const chartElement = getElement("usersChart");
  const chart = {
    height: 560, width: 1000,
    setOption(option) { this.option = option; },
    resize() { this.height = Number.parseFloat(chartElement.style.height); this.width = chartElement.clientWidth; },
    off() {}, on() {},
  };
  const context = {
    DATA: { users: Array.from({ length: 21 }, (_, i) => ({
      uid: i + 1000, name: "user" + i, files: i + 1,
      bytes: 200 - i, by_mount: { "/home": 200 - i - (i === 0 ? 50 : 0), "/data": i === 0 ? 50 : 0 },
    })) },
    userMountFilter: "", mountPaths: ["/home", "/data", "/empty"],
    mountColor: { "/home": "#123456", "/data": "#654321" },
    capUsedByMount: { "/home": 3940, "/data": 50 }, totalCapacityUsed: 3990,
    usersChart: null, usersInited: false,
    isDark: () => false, humanBytes: value => String(value), escapeHtml: value => String(value),
    colorForUid: () => "#123456",
    echarts: { init: el => {
      chart.height = Number.parseFloat(el.style.height || "560");
      chart.width = el.clientWidth;
      return chart;
    } },
    document: {
      getElementById: getElement,
      querySelector: getElement,
      querySelectorAll: () => [],
      createElement: element, createDocumentFragment: element,
    },
  };
  chartElement.clientWidth = 1000;
  globalThis.document = context.document;
  context.route = {mount:null};
  context.sort = {key:'bytes',dir:-1};
  context.ui = {theme:'light'};
  context.DATA.mounts = Object.entries(context.capUsedByMount).map(([path,scanned_bytes]) => ({path,scanned_bytes}));
  context.echarts.getInstanceByDom = () => chart;
  drawUsers(context,context.echarts);
  assert.strictEqual(chart.height, 570, "initial canvas must fit the top 20 rows");
  assert.strictEqual(chart.width, 1000);

  context.route.mount = "/data";
  chartElement.clientWidth = 640;
  drawUsers(context,context.echarts);
  assert.strictEqual(chart.height, 320, "filtering must resize the canvas to fit one row");
  assert.strictEqual(chart.width, 640, "a rerender must recover from earlier layout dimensions");
  assert.deepStrictEqual(Array.from(chart.option.yAxis.data), ["user0"]);
  assert.deepStrictEqual(Array.from(chart.option.series[0].data), [50]);
  const tbody = getElement("#usersTbl tbody");
  const filteredRow = tbody.children.at(-1).children[0].innerHTML;
  assert(filteredRow.endsWith('<td class="num">—</td>'), "a storage filter must not show the all-storage file count");
  const tooltip = chart.option.tooltip.formatter([{ dataIndex: 0, value: 50, color: "#123456", seriesName: "/data" }]);
  assert(tooltip.includes("100.0% of scanned usage on /data"), "shares must identify the scanned-usage denominator");

  context.route.mount = "/empty";
  drawUsers(context,context.echarts);
  assert.strictEqual(getElement("usersCount").textContent, "0 users on /empty", "an empty filter must clear the previous user count");

  context.route.mount = "";
  drawUsers(context,context.echarts);
  const allRow = tbody.children.at(-1).children[0].innerHTML;
  assert(allRow.endsWith('<td class="num">1</td>'), "all-storage view must retain the exact file count");
}
test('Users chart resizes and uses filtered measures',testUsersChartTracksSizeAndFilteredMeasurements);
{

  const snapshot = {
    scan_generation: "turing-2",
    mounts: [{ mount_id: "data", df_used: 400, scanned_bytes: 30 }],
    users: [{ name: "root", bytes: 30 }],
  };
  const reading = { mount_id: "data", df_used: 700, capacity_observed_unix: 123 };
  assert.strictEqual(applyObservedCapacity(snapshot, { scan_generation: "turing-1", mounts: [reading] }), snapshot);
  const observation = { scan_generation: "turing-2", mounts: [reading] };
  const updated = applyObservedCapacity(snapshot, observation);
  assert.strictEqual(updated.mounts[0].df_used, 700);
  assert.strictEqual(updated.mounts[0].scanned_bytes, 30);
  assert.strictEqual(updated.users, snapshot.users);
  assert.strictEqual(applyObservedCapacity(updated, observation), updated);
  const fallback = applyObservedCapacity(updated, { scan_generation: "turing-2", mounts: snapshot.mounts });
  assert.strictEqual(fallback.mounts[0].df_used, 400);
  assert.strictEqual(fallback.mounts[0].capacity_observed_unix, null);
}

{
  const { buildOverviewRows } = overview;
  const capacity = (used, scanTime = 100) => ({
    scan_finished_unix: scanTime,
    mounts: [{ mount_id: "data", path: "/data", df_used: used, df_total: 100, df_use_pct: used, used_pct: used }],
  });
  const cached = [{ id: "turing", snapshot: capacity(10) }];
  const rows = (snapshot, cache = cached) => buildOverviewRows([{ id: "turing", overview_snapshot: snapshot }], cache);
  assert.strictEqual(rows(capacity(90))[0].mounts[0].usedPct, 90);
  assert.strictEqual(rows(capacity(10))[0].mounts[0].usedPct, 10);
  assert.strictEqual(rows(capacity(10), [{ id: "turing", snapshot: capacity(70, 200) }])[0].mounts[0].usedPct, 70);
}


test('V2 detail renders collector severity and used_pct, skips transient errors', () => {
  globalThis.document = documentFixture();
  const mount = {mount_id:'v',path:'/data',used_pct:32,severity:'critical',df_total:100,df_used:32,df_avail:68,df_use_pct:32};
  renderHeader({hostname:'alpha', mounts:[mount],blocked:[{path:'/gone',reason:'ENOENT'}]},mount,()=>{});
  const caps = document.getElementById('caps');
  assert.equal(caps.children[0].getAttribute('data-pressure'),'critical');
  assert.match(caps.children[0].innerHTML,/32<span>%/);
  assert.equal(document.getElementById('warnBanner').innerHTML,'');
  renderHeader({hostname:'alpha',mounts:[]},null,()=>{});
  assert.equal(caps.children[0].textContent,'표시할 데이터 마운트 없음');
});

test('testDetailSharedCapacityUsesOneCapacityRowAndSeparateScanNavigation', () => {
  globalThis.document = documentFixture();
  const data = normalizeDetail({
    server_id: 'neo',
    hostname: 'neo-host',
    scanner_version: '1.0',
    run_as_root: true,
    users: [],
    top_files: [],
    stale: [],
    selected_roots: [
      { mount_id: 'home', capacity_id: 'dev-259-3', mountpoint: '/', scan_root: '/home', storage_media: 'ssd', scanned_bytes: 700 * 1024 ** 3 },
      { mount_id: 'data', capacity_id: 'dev-259-3', mountpoint: '/', scan_root: '/data', storage_media: 'ssd', scanned_bytes: 20 * 1024 },
    ],
    mounts: [
      {
        mount_id: 'home',
        path: '/home',
        mountpoint: '/',
        scan_root: '/home',
        fstype: 'ext4',
        storage_media: 'ssd',
        df_total: 1000 * 1024 ** 3,
        df_used: 400 * 1024 ** 3,
        df_avail: 600 * 1024 ** 3,
        df_use_pct: 85,
        used_pct: 85,
        severity: "warning",
        scanned_bytes: 700 * 1024 ** 3,
      },
      {
        mount_id: 'data',
        path: '/data',
        mountpoint: '/',
        scan_root: '/data',
        fstype: 'ext4',
        storage_media: 'ssd',
        df_total: 1000 * 1024 ** 3,
        df_used: 400 * 1024 ** 3 + 8192,
        df_avail: 600 * 1024 ** 3 - 8192,
        df_use_pct: 85,
        used_pct: 85,
        severity: "warning",
        scanned_bytes: 20 * 1024,
      },
    ],
  });
  function navigate({volumeId}) { renderHeader(data,data.mounts.find(m => m.mount_id === volumeId),navigate); }
  renderHeader(data,data.mounts[0],navigate);


  const caps = document.getElementById('caps');
  assert.strictEqual(caps.children.length, 1, 'detail capacity rail must group shared-capacity scan roots into one physical-disk row');
  const groupedHtml = caps.children[0].innerHTML;
  assert.strictEqual(caps.children[0].getAttribute('data-pressure'), 'warning', 'original df_use_pct must control grouped capacity pressure when byte ratio differs');
  assert.match(groupedHtml, /<div class="cap-pct"[^>]*>85<span>%<\/span>/, 'grouped capacity percentage must preserve original df_use_pct instead of recomputing from used and total');
  assert.doesNotMatch(groupedHtml, />40<span>%<\/span>/, 'grouped capacity percentage must not replace df_use_pct with the byte ratio');
  assert.match(groupedHtml, /볼륨 사용량/);
  const folderButtons = findByClass(caps.children[0], 'cap-folder-button');
  assert.strictEqual(folderButtons[0].className, 'cap-folder-button');
  assert.match(folderButtons[0].innerHTML, /700 GB<\/span>/);
  assert.match(folderButtons[1].innerHTML, /20\.0 KB<\/span>/);
  assert.deepStrictEqual(folderButtons.map(button => button.dataset.folder), ['/home', '/data'], 'folder navigation must sit under its shared volume');
  assert.strictEqual(folderButtons[0].getAttribute('aria-pressed'), 'true');
  folderButtons[1].dispatch('click');
  const nextButtons = findByClass(caps,'cap-folder-button');
  assert.strictEqual(nextButtons[1].getAttribute('aria-pressed'), 'true');
  assert.strictEqual(nextButtons[0].getAttribute('aria-pressed'), 'false');
  assert.strictEqual(document.getElementById('treemapFolderLabel').textContent, '/data · 20.0 KB');
});

test('testDetailSharedCapacityRequiresKnownConsistentMetrics', () => {
  globalThis.document = documentFixture();
  const data = normalizeDetail({
    server_id: 'neo', hostname: 'neo-host', scanner_version: '1.0', run_as_root: true,
    users: [], top_files: [], stale: [],
    selected_roots: [
      { mount_id: 'unknown-home', capacity_id: 'dev-8-1', scan_root: '/unknown-home' },
      { mount_id: 'unknown-data', capacity_id: 'dev-8-1', scan_root: '/unknown-data' },
      { mount_id: 'mismatch-home', capacity_id: 'dev-8-2', scan_root: '/mismatch-home' },
      { mount_id: 'mismatch-data', capacity_id: 'dev-8-2', scan_root: '/mismatch-data' },
      { mount_id: 'missing-pct-home', capacity_id: 'dev-8-3', scan_root: '/missing-pct-home' },
      { mount_id: 'missing-pct-data', capacity_id: 'dev-8-3', scan_root: '/missing-pct-data' },
      { mount_id: 'mismatch-avail-home', capacity_id: 'dev-8-4', scan_root: '/mismatch-avail-home' },
      { mount_id: 'mismatch-avail-data', capacity_id: 'dev-8-4', scan_root: '/mismatch-avail-data' },
    ],
    mounts: [
      { mount_id: 'unknown-home', path: '/unknown-home', fstype: 'ext4', df_total: 1000, df_used: 400, df_use_pct: 40, used_pct: 40, scanned_bytes: 100 },
      { mount_id: 'unknown-data', path: '/unknown-data', fstype: 'ext4', df_total: 1000, df_used: 400, df_use_pct: 40, used_pct: 40, scanned_bytes: 200 },
      { mount_id: 'mismatch-home', path: '/mismatch-home', fstype: 'ext4', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40, used_pct: 40, severity: "critical", scanned_bytes: 300 },
      { mount_id: 'mismatch-data', path: '/mismatch-data', fstype: 'ext4', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 41, used_pct: 41, severity: "critical", scanned_bytes: 400 },
      { mount_id: 'missing-pct-home', path: '/missing-pct-home', fstype: 'ext4', df_total: 1000, df_used: 400, df_avail: 600, scanned_bytes: 500 },
      { mount_id: 'missing-pct-data', path: '/missing-pct-data', fstype: 'ext4', df_total: 1000, df_used: 400, df_avail: 600, scanned_bytes: 600 },
      { mount_id: 'mismatch-avail-home', path: '/mismatch-avail-home', fstype: 'ext4', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40, used_pct: 40, severity: "critical", scanned_bytes: 700 },
      { mount_id: 'mismatch-avail-data', path: '/mismatch-avail-data', fstype: 'ext4', df_total: 1000, df_used: 400, df_avail: 500, df_use_pct: 40, used_pct: 40, severity: "critical", scanned_bytes: 800 },
    ],
  });
  function navigate({volumeId}) { renderHeader(data,data.mounts.find(m => m.mount_id === volumeId),navigate); }
  renderHeader(data,data.mounts[0],navigate);


  const caps = document.getElementById('caps');
  assert.strictEqual(caps.children.length, 8, 'detail must not merge same-identity rows when capacity metrics are unknown or inconsistent');
});

test('testDetailCapacityUsesCompactRowsAndFiltersBootMounts', () => {
  globalThis.document = documentFixture();
  const data = normalizeDetail({
    server_id: 'alpha-1',
    hostname: 'alpha-host',
    scanner_version: '1.0',
    run_as_root: true,
    users: [],
    top_files: [],
    stale: [],
    mounts: [
      { mount_id: 'boot', path: '/boot', fstype: 'vfat', storage_media: 'ssd', df_total: 1024, df_used: 512, df_avail: 512, df_use_pct: 50, used_pct: 50, severity: "critical" },
      { mount_id: 'root', path: '/', fstype: 'ext4', storage_media: 'ssd', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40, used_pct: 40, severity: "critical" },
      { mount_id: 'data', path: '/data', fstype: 'xfs', storage_media: 'hdd', df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
    ],
  });
  function navigate({volumeId}) { renderHeader(data,data.mounts.find(m => m.mount_id === volumeId),navigate); }
  renderHeader(data,data.mounts[0],navigate);


  const caps = document.getElementById('caps');
  assert(caps.className.split(/\s+/).includes('detail-capacity-rail'), 'detail capacity must use compact rail class');
  assert.strictEqual(caps.children.length, 2, 'boot mounts must be omitted from detail capacity rows');
  assert(caps.children.every(child => child.className.split(/\s+/).includes('detail-capacity-row')), 'each detail mount must render as a compact capacity row');
  assert.strictEqual(findByClass(caps, 'cap-scan-roots').length, 2, 'each volume must place its clickable path below the capacity');
  assert(caps.children.every(row => !row.innerHTML.includes('/data') && row.innerHTML.includes('cap-main')), 'single-path volume headers must not repeat the clickable path');
  assert.deepStrictEqual(findByClass(caps, 'cap-folder-button').map(button => button.dataset.folder), ['/', '/data']);
  const html = caps.children.map(textTree).join('\n');
  assert(!html.includes('/boot'), 'boot mount must be absent from legacy detail capacity snapshots');
  assert(html.includes('cap-path') && html.includes('/') && html.includes('/data'), 'mount paths and existing cap-path selector must be preserved');
  assert(html.includes('cap-fs') && html.includes('ext4') && html.includes('xfs'), 'filesystem values must be preserved exactly');
  assert(html.includes('cap-media') && html.includes('ssd') && html.includes('hdd'), 'media values must be exposed in the compact row');
  assert(html.includes('400 B') && html.includes('1000 B') && html.includes('1.46 KB') && html.includes('1.95 KB'), 'used/total values must be preserved exactly through existing formatting');
  assert(html.includes('40') && html.includes('75'), 'percentage values must be preserved');
  assert(html.includes('600 B') && html.includes('500 B'), 'free capacity values must be preserved');
  assert(html.includes('cap-bar') && html.includes('cap-fill'), 'thin utilization bar IDs/classes must remain available');
  assert(!caps.children.some(child => child.className.split(/\s+/).includes('cap')), 'detail capacity must no longer use the large capacity-card grid contract');

  const state = {data};
  assert.deepStrictEqual(state.data.mounts.map(m => m.path), ['/', '/data'], 'detail DATA.mounts must be filtered before downstream mount selectors derive paths');
});

test('testDetailNormalizationFiltersBootEverywhereAndRecomputesUsers', () => {
  globalThis.document = documentFixture();
  const data = normalizeDetail({
    server_id: 'alpha-1',
    hostname: 'alpha-host',
    scanner_version: '1.0',
    run_as_root: true,
    mounts: [
      { mount_id: 'boot', path: '/boot', fstype: 'vfat', df_total: 1000, df_used: 900, df_avail: 100, df_use_pct: 90, used_pct: 90, severity: "critical" },
      { mount_id: 'efi', path: '/boot/efi', fstype: 'vfat', df_total: 1000, df_used: 800, df_avail: 200, df_use_pct: 80, used_pct: 80, severity: "critical" },
      { mount_id: 'data', path: '/data', fstype: 'xfs', df_total: 5000, df_used: 1200, df_avail: 3800, df_use_pct: 24, used_pct: 24, severity: "critical" },
      { mount_id: 'bootloader', path: '/bootloader', fstype: 'xfs', df_total: 2000, df_used: 300, df_avail: 1700, df_use_pct: 15, used_pct: 15, severity: "critical" },
    ],
    users: [
      { uid: 1000, name: 'alice', bytes: 9999, files: 4, by_mount: { '/boot': 4000, '/boot/efi': 3000, '/data': 1200, '/bootloader': 300 } },
      { uid: 1001, name: 'boot-only', bytes: 7000, files: 2, by_mount: { '/boot': 7000 } },
    ],
    top_files: [
      { path: '/boot/initrd.img', bytes: 4000, uid: 1000, owner: 'alice', mtime: 1710000000, kind: 'file' },
      { path: '/boot/efi/EFI/BOOTX64.EFI', bytes: 3000, uid: 1000, owner: 'alice', mtime: 1710000000, kind: 'file' },
      { path: '/data/model.bin', bytes: 1200, uid: 1000, owner: 'alice', mtime: 1710000000, kind: 'file' },
      { path: '/bootloader/readme.txt', bytes: 300, uid: 1000, owner: 'alice', mtime: 1710000000, kind: 'file' },
    ],
    stale: [
      { path: '/boot/old-kernel', bytes: 5000, uid: 1001, owner: 'boot-only', mtime: 1600000000, age_days: 1000, kind: 'file' },
      { path: '/data/old.bin', bytes: 600, uid: 1000, owner: 'alice', mtime: 1600000000, age_days: 1000, kind: 'file' },
    ],
  });
  function navigate({volumeId}) { renderHeader(data,data.mounts.find(m => m.mount_id === volumeId),navigate); }
  renderHeader(data,data.mounts[0],navigate);


  const state = data;
  assert.deepStrictEqual(state.mounts.map(m => m.path), ['/data', '/bootloader'], 'central detail normalization must remove /boot mounts while preserving non-boot prefix paths');
  assert.deepStrictEqual(state.top_files.map(f => f.path), ['/data/model.bin', '/bootloader/readme.txt'], 'top files must not leak /boot or /boot descendants');
  assert.deepStrictEqual(state.stale.map(f => f.path), ['/data/old.bin'], 'stale files must not leak /boot or /boot descendants');
  const alice = state.users.find(u => u.uid === 1000);
  assert.deepStrictEqual(Object.keys(alice.by_mount).sort(), ['/bootloader', '/data'], 'user by_mount maps must remove /boot keys but keep non-boot prefix paths');
  assert.strictEqual(alice.bytes, 1500, 'All-user bytes must be recomputed from actionable mount paths rather than stale user bytes');
  assert.strictEqual(state.users.some(u => u.uid === 1001), false, 'boot-only users must disappear after actionable-byte normalization');
});

test('testDetailCapacityUnknownNumbersRenderNeutralDashes', () => {
  globalThis.document = documentFixture();
  const data = normalizeDetail({
    server_id: 'alpha-1', hostname: 'alpha-host', scanner_version: '1.0', run_as_root: true,
    users: [], top_files: [], stale: [],
    mounts: [{ mount_id: 'mystery', path: '/mystery', fstype: 'xfs', storage_media: 'unknown' }],
  });
  function navigate({volumeId}) { renderHeader(data,data.mounts.find(m => m.mount_id === volumeId),navigate); }
  renderHeader(data,data.mounts[0],navigate);


  const row = document.getElementById('caps').children[0];
  const html = row.innerHTML;
  assert.strictEqual(row.getAttribute('data-pressure'), 'unknown', 'missing detail capacity metrics must use neutral unknown styling');
  assert(html.includes('<span class="figure">—</span> / <span class="figure">—</span>'), 'missing used/total must render em dashes');
  assert(html.includes('<div class="cap-pct">—</div>'), 'missing percentage must render an em dash instead of invented 0%');
  assert(html.includes('<span class="figure">—</span> 여유'), 'missing available bytes must render an em dash');
  assert(!html.includes('0<span>%</span>') && !html.includes('var(--ok)'), 'unknown detail capacity must not invent healthy 0% styling');
});
