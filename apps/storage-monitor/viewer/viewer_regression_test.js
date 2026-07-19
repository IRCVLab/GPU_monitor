#!/usr/bin/env node
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class FakeClassList {
  constructor(el) { this.el = el; }
  _set(parts) { this.el.className = [...new Set(parts.filter(Boolean))].join(' '); }
  add(...names) { this._set((this.el.className || '').split(/\s+/).concat(names)); }
  remove(...names) { const drop = new Set(names); this._set((this.el.className || '').split(/\s+/).filter(n => !drop.has(n))); }
  contains(name) { return (this.el.className || '').split(/\s+/).includes(name); }
  toggle(name, force) {
    const has = this.contains(name);
    if (force === true || (!has && force !== false)) this.add(name);
    else if (has && force !== true) this.remove(name);
  }
}

class FakeElement {
  constructor(tag = 'div', ownerDocument = null) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.className = '';
    this.classList = new FakeClassList(this);
    this.attributes = {};
    this.offsetHeight = 0;
    this.offsetWidth = 0;
    this.clientHeight = 640;
    this.clientWidth = 1000;
    this.hidden = false;
    this.value = '';
    this.listeners = new Map();
    this.tabIndex = undefined;
    this.focused = false;
    this.scrollIntoViewCalls = [];
  }
  appendChild(child) {
    if (!child.ownerDocument) child.ownerDocument = this.ownerDocument;
    this.children.push(child);
    child.parentNode = this;
    return child;
  }
  removeChild(child) { this.children = this.children.filter(c => c !== child); child.parentNode = null; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  set innerHTML(value) { this._innerHTML = String(value); this.children = []; }
  get innerHTML() { return this._innerHTML || ''; }
  set textContent(value) { this._textContent = String(value); this.children = []; }
  get textContent() { return this._textContent || ''; }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'class') this.className = String(value);
    if (name === 'id') this.id = String(value);
    if (name === 'tabindex') this.tabIndex = Number(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase())] = String(value);
  }
  getAttribute(name) {
    if (name === 'class') return this.className;
    if (name === 'id') return this.id;
    return this.attributes[name];
  }
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  dispatch(type, event = {}) {
    const handler = this.listeners.get(type);
    if (handler) handler(Object.assign({ target: this, currentTarget: this, preventDefault() {} }, event));
  }
  focus() { this.focused = true; if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  scrollIntoView(options) { this.scrollIntoViewCalls.push(options || null); }
  closest(selector) {
    if (!selector || selector[0] !== '.') return null;
    const className = selector.slice(1);
    let cur = this;
    while (cur) {
      if ((cur.className || '').split(/\s+/).includes(className)) return cur;
      cur = cur.parentNode || null;
    }
    return null;
  }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}

function loadViewer() {
  const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  const scriptFiles = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)]
    .map(m => m[1])
    .filter(src => src !== 'echarts.min.js');
  assert.deepStrictEqual(scriptFiles, [
    'data-client.js',
    'selection.js',
    'treemap.js',
    'tables.js',
    'overview.js',
    'app.js',
  ], 'viewer code must be loaded from ordered external scripts including overview.js before app.js');
  assert(!/<style\b/i.test(html), 'viewer stylesheet must be externalized');
  assert(html.includes('<link rel="stylesheet" href="styles.css">'), 'index must link styles.css');
  const h1Count = (html.match(/<h1\b/gi) || []).length;
  assert.strictEqual(h1Count, 1, 'viewer must keep exactly one logical h1');
  assert(/<ul\s+id="overviewList"/.test(html), 'overview list must be a real ul');
  assert(!/id="overviewList"[^>]*role="list"/.test(html), 'overview list should rely on native list semantics instead of role=list');
  assert(html.includes('id="overviewView"'), 'overview shell must be present');
  assert(html.includes('id="overviewBack"'), 'detail back-to-overview control must be present');
  const removedName = 'ad' + 'visor';
  assert(!html.includes('data-tab="' + removedName + '"'), 'removed analysis tab must not exist');
  assert(!html.includes('id="panel-' + removedName + '"'), 'removed analysis panel must not exist');

  const elements = new Map();
  let doc = null;
  const getEl = (id) => {
    if (!elements.has(id)) {
      const el = new FakeElement('div', doc);
      el.id = id;
      elements.set(id, el);
    }
    return elements.get(id);
  };
  const historyCalls = [];
  const docListeners = new Map();
  doc = {
    createElement: (tag) => new FakeElement(tag, doc),
    getElementById: getEl,
    querySelector: () => new FakeElement('div', doc),
    querySelectorAll: () => [],
    addEventListener(type, handler) {
      const list = docListeners.get(type) || [];
      list.push(handler);
      docListeners.set(type, list);
    },
    dispatchEvent(event) {
      const list = docListeners.get(event && event.type) || [];
      for (const handler of list) handler(event);
    },
    body: new FakeElement('body', null),
    activeElement: null,
  };
  doc.body.ownerDocument = doc;
  const context = {
    console,
    Math,
    Date,
    URLSearchParams,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: (fn) => fn(),
    window: {
      innerWidth: 1200,
      innerHeight: 800,
      location: { pathname: '/viewer/index.html', search: '', hash: '' },
      history: {
        pushState: (_state, _title, href) => { historyCalls.push(['push', href]); const [pathAndQuery, hash = ''] = String(href).split('#'); const [pathname, search = ''] = pathAndQuery.split('?'); context.window.location.pathname = pathname; context.window.location.search = search ? '?' + search : ''; context.window.location.hash = hash ? '#' + hash : ''; },
        replaceState: (_state, _title, href) => { historyCalls.push(['replace', href]); const [pathAndQuery, hash = ''] = String(href).split('#'); const [pathname, search = ''] = pathAndQuery.split('?'); context.window.location.pathname = pathname; context.window.location.search = search ? '?' + search : ''; context.window.location.hash = hash ? '#' + hash : ''; },
      },
      addEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    },
    document: doc,
    navigator: { clipboard: { writeText: async () => undefined } },
    fetch: async () => ({ ok: false, json: async () => ({}) }),
    echarts: { init: () => ({ setOption() {}, off() {}, on() {}, resize() {}, dispose() {} }) },
  };
  context.global = context;
  vm.createContext(context);
  for (const src of scriptFiles) {
    const code = fs.readFileSync(path.join(__dirname, src), 'utf8');
    vm.runInContext(code, context, { filename: 'viewer/' + src });
  }
  context.__elements = elements;
  context.__historyCalls = historyCalls;
  context.__docListeners = docListeners;
  return context;
}

function textTree(node) {
  let out = node.textContent || '';
  if (node.innerHTML) out += node.innerHTML;
  for (const child of node.children || []) out += textTree(child);
  return out;
}

function numericPx(value) { return Number(String(value || '0').replace(/px$/, '')); }
async function withMutedConsole(run) {
  const originalWarn = console.warn;
  const originalError = console.error;
  console.warn = () => {};
  console.error = () => {};
  try {
    return await run();
  } finally {
    console.warn = originalWarn;
    console.error = originalError;
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
}
function hangulString(text) {
  return /[가-힣]/.test(text || '');
}

function testOverviewRenderingKeepsStableOrderAndVisibleCapacityBars() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.buildOverviewServer, 'function', 'overview row builder must be exposed');
  assert.strictEqual(typeof viewer.renderOverviewList, 'function', 'overview renderer must be exposed');

  const failedRow = viewer.buildOverviewServer(
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
        { path: '/data', df_use_pct: 95, df_avail: 800 * 1024 ** 3 },
        { path: '/archive', df_use_pct: 60, df_avail: 2 * 1024 ** 4 },
      ],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );
  const staleRow = viewer.buildOverviewServer(
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
      mounts: [{ path: '/scratch', df_use_pct: 81, df_avail: 700 * 1024 ** 3 }],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );

  const overviewList = viewer.__elements.get('overviewList') || viewer.document.getElementById('overviewList');
  const opened = [];
  viewer.renderOverviewList(overviewList, [failedRow, staleRow], { onOpenServer: (serverId) => opened.push(serverId) });

  assert.strictEqual(overviewList.children.length, 2, 'all servers must render into one dense list');
  assert.strictEqual(overviewList.children[0].tagName, 'LI', 'overview list items must use native li semantics');
  assert.strictEqual(overviewList.children[0].children[0].tagName, 'BUTTON', 'each overview li must contain a button for whole-row activation');
  assert.strictEqual(overviewList.children[0].children[0].dataset.serverId, 'beta-2', 'inventory order must not change because of severity');
  assert.strictEqual(overviewList.children[1].children[0].dataset.serverId, 'alpha-1', 'inventory order must remain stable for later rows');

  const failedButton = overviewList.children[0].children[0];
  const failedText = textTree(failedButton);
  assert(failedText.includes('beta'), 'row must contain the server display name');
  assert.strictEqual(failedButton.getAttribute('data-primary-status'), failedRow.primaryStatus.code, 'rendered primary status code must match the overview model');
  const primaryBadge = failedButton.children[0].children[1].children[0];
  assert(primaryBadge, 'primary exceptional badge must be visible');
  assert(hangulString(textTree(primaryBadge)), 'primary exceptional badge must expose visible Korean text');
  assert(primaryBadge.children[0].textContent.length > 0, 'primary exceptional badge must expose a visible shape cue');
  assert(failedText.includes('95%'), 'capacity bars must still show percentage text');
  assert(failedText.includes('800 GB'), 'capacity bars must still show available-byte text');

  const staleButton = overviewList.children[1].children[0];
  assert.strictEqual(staleButton.getAttribute('data-primary-status'), staleRow.primaryStatus.code, 'rendered stale status code must match the overview model');
  assert(hangulString(textTree(staleButton.children[0].children[1].children[0])), 'stale status badge must expose visible Korean text');
  assert(textTree(staleButton).includes('81%'), 'warning-capacity rows must still expose their compact bar text');

  failedButton.onclick();
  assert.deepStrictEqual(opened, ['beta-2'], 'clicking a row should open its detail workspace');
  let prevented = false;
  staleButton.onkeydown({ key: 'Enter', preventDefault() { prevented = true; } });
  assert.strictEqual(prevented, true, 'Enter activation must prevent duplicate default behavior');
  assert.deepStrictEqual(opened, ['beta-2', 'alpha-1'], 'Enter should activate the row');
}

function testSnapshotLoadFailureRendersAsVisibleException() {
  const viewer = loadViewer();
  const row = viewer.buildOverviewServer(
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
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
    new Error('network failed'),
  );
  assert.strictEqual(row.primaryStatus.code, 'snapshot_load_failed', 'healthy summaries with client snapshot failures must expose a dedicated exceptional status');
  const overviewList = viewer.document.getElementById('overviewList');
  viewer.renderOverviewList(overviewList, [row], { onOpenServer() {} });
  const button = overviewList.children[0].children[0];
  assert.strictEqual(button.getAttribute('data-primary-status'), 'snapshot_load_failed');
  const badge = button.children[0].children[1].children[0];
  assert(badge, 'snapshot load failures must render a visible status badge');
  assert(hangulString(textTree(badge)), 'snapshot load failure badge must render Korean text');
  assert(badge.children[0].textContent.length > 0, 'snapshot load failure badge must render a visible shape cue');
}

function testRouteNavigationAndBackShellContract() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.applyRouteState, 'function', 'route application helper must be exposed');
  assert.strictEqual(typeof viewer.navigateToOverview, 'function', 'overview navigation helper must be exposed');

  const overviewView = viewer.document.getElementById('overviewView');
  const detailView = viewer.document.getElementById('detailView');
  const back = viewer.document.getElementById('overviewBack');
  viewer.applyRouteState({ serverId: null, tab: 'treemap' }, { skipHistory: true });
  assert.strictEqual(overviewView.hidden, false, 'overview route must show the overview shell');
  assert.strictEqual(detailView.hidden, true, 'overview route must hide the detail shell');
  viewer.applyRouteState({ serverId: 'beta-2', tab: 'users' }, { skipHistory: true, skipDataLoad: true });
  assert.strictEqual(overviewView.hidden, true, 'detail route must hide the overview shell');
  assert.strictEqual(detailView.hidden, false, 'detail route must show the detail shell');
  assert.strictEqual(back.hidden, false, 'detail route must expose the back-to-overview control');
  viewer.navigateToOverview({ skipHistory: true });
  assert.strictEqual(overviewView.hidden, false, 'back navigation must restore the overview shell');
  assert.strictEqual(detailView.hidden, true, 'back navigation must hide the detail shell again');
}

async function testBootstrapDetectionIsExplicitAndSequential() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.loadBootstrapDataWith, 'function', 'bootstrap detection helper must be exposed for explicit mode tests');

  return withMutedConsole(async () => {
  let staticCalls = 0;
  let summariesCalls = 0;
  const staticBootstrap = { mode: 'static', session: {}, summaries: [{ id: 'sample' }], snapshots: [] };
  const staticResult = await viewer.loadBootstrapDataWith({
    loadSession: async () => { const err = new Error('missing'); err.status = 404; throw err; },
    loadStaticBootstrap: async () => { staticCalls += 1; return staticBootstrap; },
    loadServerSummaries: async () => { summariesCalls += 1; return []; },
    loadOrderedSnapshotsForOverview: async () => [],
    loadServerSnapshot: async () => ({})
  });
  assert.strictEqual(staticResult, staticBootstrap, 'only an exact 404 session probe may select static mode');
  assert.strictEqual(staticCalls, 1, 'static mode must be chosen explicitly from the session probe');
  assert.strictEqual(summariesCalls, 0, 'server summary fetch must not run after a static-mode decision');

  let networkStaticCalls = 0;
  await assert.rejects(() => viewer.loadBootstrapDataWith({
    loadSession: async () => { throw new Error('network down'); },
    loadStaticBootstrap: async () => { networkStaticCalls += 1; return staticBootstrap; },
    loadServerSummaries: async () => [],
    loadOrderedSnapshotsForOverview: async () => [],
    loadServerSnapshot: async () => ({})
  }), /network down/, 'status-less session failures must surface instead of falling back to static data');
  assert.strictEqual(networkStaticCalls, 0, 'status-less session failures must not select static mode');

  let apiStaticCalls = 0;
  await assert.rejects(() => viewer.loadBootstrapDataWith({
    loadSession: async () => ({ authenticated: true, can_rescan: false, csrf_token: 'csrf' }),
    loadStaticBootstrap: async () => { apiStaticCalls += 1; return staticBootstrap; },
    loadServerSummaries: async () => { throw new Error('servers unavailable'); },
    loadOrderedSnapshotsForOverview: async () => [],
    loadServerSnapshot: async () => ({})
  }), /servers unavailable/, 'after /api/session succeeds, later API failures must remain API failures');
  assert.strictEqual(apiStaticCalls, 0, 'post-session API failures must never fall back to static samples');
  });
}

async function testDetailNavigationGuardsAgainstStaleAsyncCompletion() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.rememberBootstrap, 'function', 'bootstrap state setter must be exposed for navigation tests');
  assert.strictEqual(typeof viewer.ensureDetailLoaded, 'function', 'detail loader must be exposed for race regressions');
  assert.strictEqual(typeof viewer.getCurrentDetailDebugState, 'function', 'detail debug getter must be exposed for race regressions');

  return withMutedConsole(async () => {
  const alpha = deferred();
  const beta = deferred();
  viewer.loadSnapshotForCurrentSource = (serverId) => {
    if (serverId === 'alpha-1') return alpha.promise;
    if (serverId === 'beta-2') return beta.promise;
    throw new Error('unexpected server ' + serverId);
  };
  viewer.rememberBootstrap({
    mode: 'api',
    session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: [
      { id: 'alpha-1', display_name: 'alpha', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
      { id: 'beta-2', display_name: 'beta', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    ],
    snapshots: [],
  });

  viewer.navigateToServer('alpha-1', { skipHistory: true });
  viewer.navigateToServer('beta-2', { skipHistory: true });

  alpha.resolve({ server_id: 'alpha-1', hostname: 'alpha-host', mounts: [], users: [], top_files: [], stale: [] });
  await flushPromises();
  const afterAlpha = viewer.getCurrentDetailDebugState();
  assert.strictEqual(afterAlpha.currentServerId, 'beta-2', 'late alpha completion must not change the current detail target');
  assert.notStrictEqual(afterAlpha.data && afterAlpha.data.hostname, 'alpha-host', 'late alpha completion must not assign alpha data into beta detail');
  assert.strictEqual(viewer.document.getElementById('detailError').hidden, true, 'late alpha completion must not surface alpha errors into beta detail');

  beta.resolve({ server_id: 'beta-2', hostname: 'beta-host', mounts: [], users: [], top_files: [], stale: [] });
  await flushPromises();
  const afterBeta = viewer.getCurrentDetailDebugState();
  assert.strictEqual(afterBeta.currentServerId, 'beta-2');
  assert.strictEqual(afterBeta.data && afterBeta.data.hostname, 'beta-host', 'beta detail should render once beta completes');

  const rejectAlpha = deferred();
  const slowBeta = deferred();
  viewer.loadSnapshotForCurrentSource = (serverId) => serverId === 'alpha-1' ? rejectAlpha.promise : slowBeta.promise;
  viewer.navigateToServer('alpha-1', { skipHistory: true, forceReload: true });
  viewer.navigateToServer('beta-2', { skipHistory: true, forceReload: true });
  rejectAlpha.reject(new Error('alpha exploded'));
  await flushPromises();
  assert.strictEqual(viewer.document.getElementById('detailError').hidden, true, 'late alpha failure must not replace beta detail with an alpha error');
  slowBeta.resolve({ server_id: 'beta-2', hostname: 'beta-host-2', mounts: [], users: [], top_files: [], stale: [] });
  await flushPromises();
  assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'beta-host-2');
  });
}


function overviewRowText(viewer, serverId) {
  const list = viewer.document.getElementById('overviewList');
  const item = (list.children || []).find(child => child.children && child.children[0] && child.children[0].dataset.serverId === serverId);
  return item ? textTree(item) : '';
}

async function testOlderSameServerSuccessCannotOverrideNewerSuccess() {
  const viewer = loadViewer();
  const older = deferred();
  const newer = deferred();
  let callCount = 0;
  viewer.loadSnapshotForCurrentSource = (serverId) => {
    assert.strictEqual(serverId, 'alpha-1');
    callCount += 1;
    return callCount === 1 ? older.promise : newer.promise;
  };
  viewer.rememberBootstrap({
    mode: 'api',
    session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: [
      { id: 'alpha-1', display_name: 'alpha', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    ],
    snapshots: [],
  });
  viewer.renderOverview();

  await withMutedConsole(async () => {
    viewer.navigateToServer('alpha-1', { skipHistory: true, forceReload: true });
    viewer.navigateToServer('alpha-1', { skipHistory: true, forceReload: true });
    newer.resolve({ server_id: 'alpha-1', hostname: 'alpha-new', mounts: [{ path: '/data', df_use_pct: 91, df_avail: 700 * 1024 ** 3 }], users: [], top_files: [], stale: [] });
    await flushPromises();
    assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'alpha-new', 'newer alpha request should win the detail data');
    assert(overviewRowText(viewer, 'alpha-1').includes('91%'), 'overview should reflect the newer alpha snapshot');

    older.resolve({ server_id: 'alpha-1', hostname: 'alpha-old', mounts: [{ path: '/data', df_use_pct: 11, df_avail: 900 * 1024 ** 3 }], users: [], top_files: [], stale: [] });
    await flushPromises();
  });

  assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'alpha-new', 'obsolete older alpha success must not replace newer detail data');
  assert(overviewRowText(viewer, 'alpha-1').includes('91%'), 'obsolete older alpha success must not rewrite overview status or mount metrics');
  viewer.navigateToOverview({ skipHistory: true });
  viewer.navigateToServer('alpha-1', { skipHistory: true, skipDataLoad: true });
  assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'alpha-new', 'obsolete older alpha success must not replace cached snapshot data');
}

async function testOlderSameServerFailureCannotOverrideNewerSuccess() {
  const viewer = loadViewer();
  const older = deferred();
  const newer = deferred();
  let callCount = 0;
  viewer.loadSnapshotForCurrentSource = (serverId) => {
    assert.strictEqual(serverId, 'alpha-1');
    callCount += 1;
    return callCount === 1 ? older.promise : newer.promise;
  };
  viewer.rememberBootstrap({
    mode: 'api',
    session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: [
      { id: 'alpha-1', display_name: 'alpha', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    ],
    snapshots: [],
  });
  viewer.renderOverview();

  await withMutedConsole(async () => {
    viewer.navigateToServer('alpha-1', { skipHistory: true, forceReload: true });
    viewer.navigateToServer('alpha-1', { skipHistory: true, forceReload: true });
    newer.resolve({ server_id: 'alpha-1', hostname: 'alpha-fresh', mounts: [{ path: '/data', df_use_pct: 77, df_avail: 800 * 1024 ** 3 }], users: [], top_files: [], stale: [] });
    await flushPromises();
    assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'alpha-fresh');
    older.reject(new Error('stale alpha failed'));
    await flushPromises();
  });

  assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'alpha-fresh', 'obsolete older alpha failure must not clear newer detail data');
  assert.strictEqual(viewer.document.getElementById('detailError').hidden, true, 'obsolete older alpha failure must not surface an error');
  assert(!overviewRowText(viewer, 'alpha-1').includes('불러오기 실패'), 'obsolete older alpha failure must not rewrite overview status to load failure');
  viewer.navigateToOverview({ skipHistory: true });
  viewer.navigateToServer('alpha-1', { skipHistory: true, skipDataLoad: true });
  assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'alpha-fresh', 'obsolete older alpha failure must not replace cached snapshot data');
}

function testDeleteCommandQuoting() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.shellQuotePath, 'function', 'shellQuotePath must be exposed for command generation');
  assert.strictEqual(typeof viewer.validateCleanupSelection, 'function', 'cleanup selection validation must be exposed for command generation');
  assert.strictEqual(typeof viewer.buildCleanupCommandPlan, 'function', 'fixed cleanup command planning must be exposed for command generation');
  assert.strictEqual(typeof viewer.cleanupCheckboxHtml, 'function', 'cleanupCheckboxHtml must be exposed for table rows');
  assert.strictEqual(typeof viewer.renderCleanupPanel, 'function', 'renderCleanupPanel must be exposed for selection updates');
  assert.strictEqual(typeof viewer.bindCleanupSelection, 'function', 'bindCleanupSelection must be exposed before app init');
  assert.strictEqual(viewer.shellQuotePath('/data/simple file.bin'), "'/data/simple file.bin'");
  assert.strictEqual(viewer.shellQuotePath("/data/O'Hara/checkpoint.pt"), "'/data/O'\"'\"'Hara/checkpoint.pt'");

  const snapshot = {
    server_id: 'alpha-1',
    selected_roots: [
      { mount_id: 'data', mount_root: '/', mountpoint: '/data', scan_root: '/data', status: 'complete' },
    ],
    mounts: [
      { mount_id: 'data', path: '/data', scan_root: '/data' },
    ],
  };
  const validation = viewer.validateCleanupSelection(snapshot, { path: "/data/O'Hara/checkpoint.pt", kind: 'file' });
  assert.strictEqual(validation.accepted, true, 'file selections inside selected scan roots must be accepted');

  const plan = viewer.buildCleanupCommandPlan(snapshot, { path: "/data/O'Hara/checkpoint.pt", kind: 'file' }, { freshness: 'stale', revealDestructive: false });
  assert.deepStrictEqual(Array.from(plan.inspectionCommands, entry => entry.command), [
    "sudo du -shx -- '/data/O'\"'\"'Hara/checkpoint.pt'",
    "sudo find '/data/O'\"'\"'Hara/checkpoint.pt' -xdev \\( -type f -o -type d \\) -printf '%s\\t%TY-%Tm-%Td %TH:%TM\\t%p\\n' | sort -nr | head -n 20",
    "sudo stat -- '/data/O'\"'\"'Hara/checkpoint.pt'",
    "sudo find '/data/O'\"'\"'Hara/checkpoint.pt' -xdev -printf '%TY-%Tm-%Td %TH:%TM\\t%s\\t%p\\n' | sort -r | head -n 20",
  ]);
  assert.strictEqual(plan.destructiveVisible, false, 'destructive command must stay hidden by default');
  assert.strictEqual(plan.destructiveCommand.command, "sudo rm -i -- '/data/O'\"'\"'Hara/checkpoint.pt'");
  assert.ok(plan.warnings.some(w => /stale/i.test(w)), 'stale snapshot warnings must stay visible in the plan');
}

function testTreemapCleanupModeBindsCleanupRenderedListenerOnce() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.bindTreemapCleanupMode, 'function', 'treemap cleanup binding must be exposed');
  viewer.bindTreemapCleanupMode();
  viewer.bindTreemapCleanupMode();
  const listeners = viewer.__docListeners.get('cleanup-selection-rendered') || [];
  assert.strictEqual(listeners.length, 1, 'cleanup-selection-rendered listener must not duplicate across repeated bindTreemapCleanupMode calls');
}

function testCleanupSelectionLifecycleResetsRevealOnPathAndServerChange() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.setCleanupSelectedItem, 'function', 'cleanup selection setter must be exposed');
  assert.strictEqual(typeof viewer.resetCleanupSelectionState, 'function', 'cleanup selection reset must be exposed');
  assert.strictEqual(typeof viewer.buildCleanupCommandPlan, 'function', 'cleanup command planning must be exposed');

  viewer.rememberBootstrap({
    mode: 'api',
    session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: [
      { id: 'alpha-1', display_name: 'alpha', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
      { id: 'beta-2', display_name: 'beta', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    ],
    snapshots: [],
  });

  viewer.applyRouteState({ serverId: 'alpha-1', tab: 'treemap' }, { skipHistory: true, skipDataLoad: true });
  viewer.DATA = {
    server_id: 'alpha-1',
    selected_roots: [{ mount_id: 'data', mount_root: '/', mountpoint: '/data', scan_root: '/data', status: 'complete' }],
    mounts: [{ mount_id: 'data', path: '/data', scan_root: '/data' }],
  };

  assert.strictEqual(viewer.setCleanupSelectedItem({ path: '/data/one', kind: 'file', bytes: 10, source: 'top' }, true), true, 'first valid selection should be accepted');
  viewer.cleanupSelectionState.revealDestructive = true;
  let plan = viewer.renderCleanupPanel();
  assert.strictEqual(plan.destructiveVisible, true, 'test fixture should be able to expose the destructive command after explicit reveal');

  assert.strictEqual(viewer.setCleanupSelectedItem({ path: '/data/two', kind: 'file', bytes: 20, source: 'top' }, true), true, 'changing to a different valid selection should be accepted');
  plan = viewer.renderCleanupPanel();
  assert.strictEqual(plan.path, '/data/two');
  assert.strictEqual(plan.destructiveVisible, false, 'changing the selected path must reset destructive reveal state');

  viewer.applyRouteState({ serverId: 'beta-2', tab: 'treemap' }, { skipHistory: true, skipDataLoad: true });
  assert.strictEqual(viewer.document.getElementById('cleanupPanel').getAttribute('aria-hidden'), 'true', 'switching servers must hide the cleanup panel until a fresh selection is made');
}

function testTreemapHidesMicroTilesInsteadOfInflatingThem() {
  const viewer = loadViewer();
  const GiB = 1024 ** 3;
  const MiB = 1024 ** 2;
  const root = {
    name: '/data',
    bytes: 600 * GiB + 600 * MiB,
    other_bytes: 0,
    children: [
      { name: 'big-600GiB', bytes: 600 * GiB, uid: 1001, files: 1 },
      { name: 'tiny-600MiB', bytes: 600 * MiB, uid: 1002, files: 1 },
    ],
  };
  const el = new FakeElement('div');
  viewer.layoutTreemap(el, root, 0, 0, 1000, 600, 0, '/data');

  const tiles = el.children.filter(c => (c.className || '').includes('tmtile'));
  const big = tiles.find(c => c._tip && c._tip.path.endsWith('/big-600GiB'));
  const tiny = tiles.find(c => c._tip && c._tip.path.endsWith('/tiny-600MiB'));
  assert.ok(big, 'large item should render as a tile');
  assert.ok(!tiny, '600MiB beside 600GiB should be hidden/grouped, not inflated into a misleading tile');
  assert.ok(el._tmHiddenItems, 'hidden tiny items should be summarized on the container');
  assert.strictEqual(el._tmHiddenItems.bytes, 600 * MiB);
  assert.strictEqual(el._tmHiddenItems.count, 1);

  const bigArea = numericPx(big.style.width) * numericPx(big.style.height);
  assert.ok(bigArea > 590000, `large tile should retain almost all proportional area; got ${bigArea}`);
}

function testTreemapTilesUseSelectionModeInsteadOfPerTileCheckboxes() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.isTreemapCleanupGesture, 'function', 'treemap cleanup gesture must be detectable');
  assert.strictEqual(typeof viewer.setTreemapModifierActive, 'function', 'temporary shortcut state must be controllable');
  assert.strictEqual(typeof viewer.treemapShortcutCopy, 'function', 'shortcut copy should be centralized and testable');
  assert.strictEqual(typeof viewer.isTopLevelCleanupPath, 'function', 'top-level cleanup path guard must be exposed');
  assert.strictEqual(viewer.isTreemapCleanupGesture({ ctrlKey: true }), true, 'Ctrl-click should select cleanup items');
  assert.strictEqual(viewer.isTreemapCleanupGesture({ metaKey: true }), true, 'Command-click should select cleanup items');
  assert.strictEqual(viewer.isTreemapCleanupGesture({}), false, 'plain click should not select cleanup items');
  viewer.setTreemapModifierActive(true);
  assert.strictEqual(viewer.isTreemapCleanupGesture({}), true, 'holding the shortcut should temporarily enable selection mode');
  viewer.setTreemapModifierActive(false);
  assert.strictEqual(viewer.isTopLevelCleanupPath('/home'), true, 'absolute top-level directories are too broad to select');
  assert.strictEqual(viewer.isTopLevelCleanupPath('/home/project'), false, 'deeper paths may be selected');
  const idleCopy = viewer.treemapShortcutCopy(false);
  assert.strictEqual(idleCopy.label, 'Ctrl/⌘ click: inspect');
  assert.strictEqual(idleCopy.hint, 'Click: drill · Ctrl/⌘ click: inspect path · 클릭: 열기 · Ctrl/⌘ 클릭: 경로 점검');
  const activeCopy = viewer.treemapShortcutCopy(true);
  assert.strictEqual(activeCopy.label, 'Selecting… / 선택 중');
  assert.strictEqual(activeCopy.hint, 'Release Ctrl/⌘ to leave selection mode · 키를 떼면 선택 모드 종료');

  viewer.DATA = {
    server_id: 'alpha-1',
    selected_roots: [
      { mount_id: 'data', mount_root: '/', mountpoint: '/data', scan_root: '/data', status: 'complete' },
    ],
    mounts: [
      { mount_id: 'data', path: '/data', scan_root: '/data' },
    ],
  };

  const root = {
    name: '/data',
    kind: 'directory',
    bytes: 600,
    other_bytes: 100,
    uid: 1001,
    children: [
      {
        name: 'project',
        kind: 'directory',
        bytes: 500,
        uid: 1001,
        files: 20,
        children: [
          { name: 'nested', kind: 'directory', bytes: 200, uid: 1001, files: 5, children: [] },
        ],
      },
    ],
  };
  const el = new FakeElement('div');
  viewer.layoutTreemap(el, root, 0, 0, 600, 400, 0, '/data');

  const tiles = el.children.filter(c => (c.className || '').includes('tmtile'));
  const project = tiles.find(c => c._tip && c._tip.path === '/data/project');
  const other = tiles.find(c => c._tip && c._tip.other);
  assert.ok(project, 'real-path tile should render');
  assert.ok(other, 'aggregate other-small-files tile should render for this fixture');

  const projectCleanup = project.children.find(c => c.className === 'tm-cleanup');
  assert.ok(!projectCleanup, 'treemap must not clutter every tile with a visible checkbox overlay');
  assert.strictEqual(project.dataset.cleanupPath, '/data/project', 'real-path tile should carry cleanup metadata for selection mode');
  assert.strictEqual(project.dataset.cleanupKind, 'directory', 'treemap selection metadata must retain snapshot kind');
  assert.strictEqual(project.getAttribute('role'), 'button', 'selectable/drillable treemap tiles must expose button semantics');
  assert.strictEqual(project.tabIndex, 0, 'selectable/drillable treemap tiles must be keyboard focusable');
  assert.strictEqual(project.getAttribute('aria-selected'), 'false', 'unselected selectable tiles must expose aria-selected=false');
  assert.match(project.getAttribute('aria-label'), /Click or Enter drills into \/data\/project/, 'drillable treemap tile label must describe primary drill behavior');
  assert.match(project.getAttribute('aria-label'), /Ctrl\/Command click or selection mode inspects \/data\/project/, 'drillable treemap tile label must describe modifier/selection-mode inspection');
  assert.ok(project.children.some(c => c.className === 'tm-cleanup-badge'), 'real-path tile should have a hidden selected-state badge');
  assert.ok(!other.dataset.cleanupPath, 'aggregate non-path tiles must not be selectable');
  assert.ok(!other.children.some(c => c.className === 'tm-cleanup-badge'), 'aggregate non-path tiles must not show cleanup controls');

  let drillCount = 0;
  let inspectCount = 0;
  viewer.renderTreemap = () => { drillCount += 1; };
  viewer.toggleCleanupSelectedItem = () => { inspectCount += 1; return true; };
  let enterPrevented = false;
  project.onkeydown({ key: 'Enter', stopPropagation() {}, preventDefault() { enterPrevented = true; } });
  assert.strictEqual(enterPrevented, true, 'Enter on a button-like tile must prevent default browser behavior');
  assert.strictEqual(drillCount, 1, 'Enter must preserve primary drill behavior when cleanup mode is off');
  assert.strictEqual(inspectCount, 0, 'Enter must not inspect while cleanup mode is off');

  viewer.setTreemapModifierActive(true);
  let spacePrevented = false;
  project.onkeydown({ key: ' ', stopPropagation() {}, preventDefault() { spacePrevented = true; } });
  assert.strictEqual(spacePrevented, true, 'Space on a button-like tile must prevent default browser scrolling');
  assert.strictEqual(drillCount, 1, 'keyboard activation in cleanup mode must stop drilling');
  assert.strictEqual(inspectCount, 1, 'keyboard activation in cleanup mode must inspect/select instead of drilling');
}

function testTreemapGroupTilesStayBehindDescendants() {
  const viewer = loadViewer();
  const root = {
    name: '/',
    bytes: 700,
    other_bytes: 0,
    uid: 0,
    children: [
      {
        name: 'home',
        bytes: 700,
        uid: 0,
        files: 10,
        children: [
          { name: 'project', bytes: 650, uid: 1001, files: 9 },
        ],
      },
    ],
  };
  const el = new FakeElement('div');
  viewer.layoutTreemap(el, root, 0, 0, 700, 420, 0, '/');

  const group = el.children.find(c => c._tip && c._tip.path === '/home');
  const child = el.children.find(c => c._tip && c._tip.path === '/home/project');
  assert.ok(group && (group.className || '').includes('tmgroup'), 'parent group should render');
  assert.ok(child, 'child tile should render above parent group');
  assert.strictEqual(group.style.zIndex, '1', 'group background/header must stay in lower stacking layer');
  assert.strictEqual(child.style.zIndex, '2', 'descendant tiles must stay above group backgrounds');
}

function testTreemapDoesNotExposeTopLevelCleanupCandidates() {
  const viewer = loadViewer();
  const root = {
    name: '/',
    bytes: 600,
    other_bytes: 0,
    uid: 0,
    children: [
      { name: 'home', bytes: 500, uid: 0, files: 20, children: [] },
    ],
  };
  const el = new FakeElement('div');
  viewer.layoutTreemap(el, root, 0, 0, 600, 400, 0, '/');

  const home = el.children.find(c => c._tip && c._tip.path === '/home');
  assert.ok(home, 'top-level tile should still render visually');
  assert.ok(!home.dataset.cleanupPath, 'top-level tile must not be selectable for cleanup commands');
  assert.ok(!home.children.some(c => c.className === 'tm-cleanup-badge'), 'top-level tile must not show selected-state badge');
}

function testTreemapScaleNoteExplainsHiddenTinyItemsBilingually() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.renderTreemapScaleNote, 'function', 'scale note renderer should be testable');
  const el = new FakeElement('div');
  el._tmHiddenItems = { bytes: 1280, count: 39 };
  viewer.renderTreemapScaleNote(el);
  const note = el.children.find(c => c.className === 'tm-scale-note');
  assert.ok(note, 'scale note should be rendered when tiny items are hidden');
  assert.ok(note.innerHTML.includes('too small to draw proportionally'), 'English note should explain why items are hidden');
  assert.ok(note.innerHTML.includes('실제 비율'), 'Korean note should explain true-scale hiding');
}

function testCleanupPanelResponsiveCssAndDangerListSemantics() {
  const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');
  assert(/<(ol|ul)\s+id="cleanupDangerCommand"/.test(html), 'destructive cleanup command container must be a real list to avoid orphan list items');
  assert(/#cleanupPanel\b[\s\S]*max-height:\s*calc\(100vh\s*-/.test(css), 'cleanup panel must keep a fallback viewport-bounded max-height');
  assert(/#cleanupPanel\b[\s\S]*max-height:\s*calc\(100dvh\s*-/.test(css), 'cleanup panel must keep a modern dynamic-viewport max-height');
  assert(/#cleanupPanel\b[\s\S]*overflow-y:\s*auto/.test(css), 'cleanup panel must scroll internally when taller than the viewport');
  assert(/#cleanupPanel\b[\s\S]*overscroll-behavior:\s*contain/.test(css), 'cleanup panel must contain overscroll inside the panel');
  assert(/env\(safe-area-inset-top\)/.test(css) && /env\(safe-area-inset-bottom\)/.test(css), 'cleanup panel viewport bounds must include mobile safe-area insets');
  assert(/#cleanupDangerCommand\b[\s\S]*list-style:\s*none/.test(css) || /\.cleanup-command-list\b[\s\S]*list-style:\s*none/.test(css), 'cleanup command lists must reset list bullets');
}

function testCleanupPanelRevealScrollsFocusedControlsIntoView() {
  const viewer = loadViewer();
  viewer.currentServerId = 'alpha-1';
  viewer.currentServerSummary = { freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete' };
  viewer.DATA = {
    server_id: 'alpha-1',
    selected_roots: [{ mount_id: 'data', mount_root: '/', mountpoint: '/data', scan_root: '/data', status: 'complete' }],
    mounts: [{ mount_id: 'data', path: '/data', scan_root: '/data' }],
  };
  viewer.setCleanupSelectedItem({ path: '/data/project', kind: 'directory', bytes: 100, source: 'treemap' }, true);
  const reveal = viewer.document.getElementById('cleanupReveal');
  const danger = viewer.document.getElementById('cleanupDangerCommand');
  assert.ok(reveal, 'reveal control must exist');
  assert.ok(danger, 'destructive command list must exist');
  viewer.cleanupSelectionState.revealDestructive = true;
  viewer.renderCleanupPanel();
  assert.ok(reveal.scrollIntoViewCalls.length > 0 || danger.scrollIntoViewCalls.length > 0, 'revealing the destructive command must scroll controls into view inside the cleanup panel');
}

async function main() {
  testOverviewRenderingKeepsStableOrderAndVisibleCapacityBars();
  testSnapshotLoadFailureRendersAsVisibleException();
  testRouteNavigationAndBackShellContract();
  await testBootstrapDetectionIsExplicitAndSequential();
  await testDetailNavigationGuardsAgainstStaleAsyncCompletion();
  await testOlderSameServerSuccessCannotOverrideNewerSuccess();
  await testOlderSameServerFailureCannotOverrideNewerSuccess();
  testDeleteCommandQuoting();
  testTreemapCleanupModeBindsCleanupRenderedListenerOnce();
  testCleanupSelectionLifecycleResetsRevealOnPathAndServerChange();
  testTreemapHidesMicroTilesInsteadOfInflatingThem();
  testTreemapTilesUseSelectionModeInsteadOfPerTileCheckboxes();
  testTreemapGroupTilesStayBehindDescendants();
  testTreemapDoesNotExposeTopLevelCleanupCandidates();
  testTreemapScaleNoteExplainsHiddenTinyItemsBilingually();
  testCleanupPanelResponsiveCssAndDangerListSemantics();
  testCleanupPanelRevealScrollsFocusedControlsIntoView();
  console.log('viewer regression tests passed');
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
