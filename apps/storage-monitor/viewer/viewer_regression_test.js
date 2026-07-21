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
  assert(html.includes('<p id="sampleDataMarker" class="sample-data-marker" hidden>샘플 데이터</p>'), 'overview must include the initially hidden explicit sample-data marker');
  assert(!html.includes('id="overviewAggregate"'), 'compact overview must remove the page aggregate container');
  assert(/<ul\s+id="overviewList"/.test(html), 'overview list must be a real ul');
  assert(!/id="overviewList"[^>]*role="list"/.test(html), 'overview list should rely on native list semantics instead of role=list');
  assert(html.includes('id="overviewView"'), 'overview shell must be present');
  assert(!html.includes('class="overview-lead"'), 'compact overview lead must be absent from markup');
  assert(!html.includes('서버와 마운트 순서는 입력 순서를 그대로 따릅니다'), 'compact overview must not keep the explanatory lead copy in markup');
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
  const documentElement = new FakeElement('html', doc);
  documentElement.dataset = {};
  doc = {
    documentElement,
    cookie: '',
    createElement: (tag) => new FakeElement(tag, doc),
    createDocumentFragment: () => new FakeElement('fragment', doc),
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
  doc.documentElement.ownerDocument = doc;
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

function findAll(node, predicate, out = []) {
  if (node && predicate(node)) out.push(node);
  for (const child of (node && node.children) || []) findAll(child, predicate, out);
  return out;
}

function findByClass(node, className) {
  return findAll(node, el => (el.className || '').split(/\s+/).includes(className));
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
        { path: '/data', df_total: 1000 * 1024 ** 3, df_used: 950 * 1024 ** 3, df_use_pct: 95, df_avail: 800 * 1024 ** 3 },
        { path: '/archive', df_total: 5 * 1024 ** 4, df_used: 3 * 1024 ** 4, df_use_pct: 60, df_avail: 2 * 1024 ** 4 },
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
      mounts: [{ path: '/scratch', df_total: 1000 * 1024 ** 3, df_used: 810 * 1024 ** 3, df_use_pct: 81, df_avail: 700 * 1024 ** 3 }],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );

  const overviewList = viewer.__elements.get('overviewList') || viewer.document.getElementById('overviewList');
  const opened = [];
  viewer.renderOverviewList(overviewList, [failedRow, staleRow], { onOpenServer: (serverId) => opened.push(serverId) });

  assert.strictEqual(overviewList.children.length, 2, 'all servers must render into one dense list');
  assert.strictEqual(overviewList.children[0].tagName, 'LI', 'overview list items must use native li semantics');
  assert.strictEqual(overviewList.children[0].children[0].tagName, 'A', 'each overview li must contain a native monitor-card link');
  assert.strictEqual(overviewList.children[0].children[0].dataset.serverId, 'beta-2', 'inventory order must not change because of severity');
  assert.strictEqual(overviewList.children[1].children[0].dataset.serverId, 'alpha-1', 'inventory order must remain stable for later rows');

  const failedButton = overviewList.children[0].children[0];
  const failedText = textTree(failedButton);
  assert(failedText.includes('beta'), 'row must contain the server display name');
  assert.strictEqual(failedButton.getAttribute('data-primary-status'), failedRow.primaryStatus.code, 'rendered primary status code must match the overview model');
  const primaryState = findByClass(failedButton, 'overview-card-state')[0];
  assert(primaryState, 'primary exceptional state must remain visible in the compact card header');
  assert(hangulString(textTree(primaryState)), 'primary exceptional state must expose visible Korean text');
  assert.strictEqual(findByClass(failedButton, 'overview-status-dot').length, 1, 'primary exceptional state must include a compact shape cue');
  assert(failedText.includes('95%'), 'capacity bars must still show percentage text');
  assert(failedText.includes('800 GB'), 'capacity bars must still show available-byte text');

  const staleButton = overviewList.children[1].children[0];
  assert.strictEqual(staleButton.getAttribute('data-primary-status'), staleRow.primaryStatus.code, 'rendered stale status code must match the overview model');
  assert(hangulString(textTree(findByClass(staleButton, 'overview-card-state')[0])), 'stale state must expose visible Korean text');
  assert(textTree(staleButton).includes('81%'), 'warning-capacity rows must still expose their compact bar text');

  failedButton.onclick();
  assert.deepStrictEqual(opened, ['beta-2'], 'clicking a row should open its detail workspace');
  let prevented = false;
  staleButton.onkeydown({ key: ' ', preventDefault() { prevented = true; } });
  assert.strictEqual(prevented, true, 'Space activation must prevent page scrolling');
  assert.deepStrictEqual(opened, ['beta-2', 'alpha-1'], 'Space should activate the card link');
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
  const state = findByClass(button, 'overview-card-state')[0];
  assert(state, 'snapshot load failures must render a visible compact state');
  assert(hangulString(textTree(state)), 'snapshot load failure state must render Korean text');
  assert.strictEqual(findByClass(button, 'overview-status-dot').length, 1, 'snapshot load failure state must render a visible shape cue');
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

  const orderedSeen = [];
  const apiEnvelope = await viewer.loadBootstrapDataWith({
    loadSession: async () => ({ authenticated: true, can_rescan: false, csrf_token: 'csrf' }),
    loadStaticBootstrap: async () => { throw new Error('static must not be used after API session succeeds'); },
    loadServerSummaries: async () => ({ data_mode: 'sample', servers: [{ id: 'hinton' }, { id: 'atlas' }] }),
    loadOrderedSnapshotsForOverview: async (summaries) => {
      orderedSeen.push(...summaries.map(row => row.id));
      return summaries.map(row => ({ id: row.id, snapshot: { server_id: row.id, mounts: [] }, error: null }));
    },
    loadServerSnapshot: async () => ({})
  });
  assert.strictEqual(apiEnvelope.dataMode, 'sample', 'API bootstrap must carry normalized dataMode from the /api/servers envelope');
  assert.deepStrictEqual(apiEnvelope.summaries.map(row => row.id), ['hinton', 'atlas'], 'API bootstrap must pass only the ordered server rows downstream');
  assert.deepStrictEqual(orderedSeen, [], 'API bootstrap must leave large snapshot loading deferred until after summary rendering');
  await apiEnvelope.startSnapshotLoading(() => {});
  assert.deepStrictEqual(orderedSeen, ['hinton', 'atlas'], 'snapshot loading must receive the envelope servers in original order');
  });
}

async function testApiBootstrapRendersBeforeSlowSnapshotsAndStreamsCompletedServers() {
  const viewer = loadViewer();
  let releaseSnapshots;
  const snapshotGate = new Promise(resolve => { releaseSnapshots = resolve; });
  let snapshotLoadingStarted = false;
  const streamed = [];

  const pendingBootstrap = viewer.loadBootstrapDataWith({
    loadSession: async () => ({ authenticated: false, can_rescan: false, csrf_token: '' }),
    loadServerSummaries: async () => ({
      data_mode: 'inventory',
      servers: [{ id: 'alpha-1', display_name: 'alpha', order: 1, mount_count: 1 }],
    }),
    loadOrderedSnapshotsForOverview: async (summaries, snapshotLoader, onEntry) => {
      snapshotLoadingStarted = true;
      await snapshotGate;
      const entry = { id: summaries[0].id, snapshot: { server_id: summaries[0].id, mounts: [] }, error: null };
      onEntry(entry);
      return [entry];
    },
    loadServerSnapshot: async () => ({})
  });

  const outcome = await Promise.race([
    pendingBootstrap.then(bootstrap => ({ kind: 'bootstrap', bootstrap })),
    new Promise(resolve => setTimeout(() => resolve({ kind: 'timeout' }), 30)),
  ]);
  releaseSnapshots();
  assert.strictEqual(outcome.kind, 'bootstrap', 'API bootstrap must not wait for large server snapshots before first render');
  const bootstrap = outcome.bootstrap;
  assert(Array.isArray(bootstrap.snapshots) && bootstrap.snapshots.length === 0, 'first render must begin from lightweight server summaries');
  assert.strictEqual(snapshotLoadingStarted, false, 'snapshot downloads must start only after the caller renders summaries');
  assert.strictEqual(typeof bootstrap.startSnapshotLoading, 'function', 'API bootstrap must expose deferred snapshot hydration');

  const entries = await bootstrap.startSnapshotLoading(entry => streamed.push(entry.id));
  assert.deepStrictEqual(streamed, ['alpha-1'], 'each completed snapshot must be streamable into its existing card');
  assert.deepStrictEqual(entries.map(entry => entry.id), ['alpha-1'], 'background hydration must preserve server order');
}

async function testApiBootstrapUsesEmbeddedOverviewSnapshotsWithoutFullDownloads() {
  const viewer = loadViewer();
  let fullSnapshotLoads = 0;
  const overviewSnapshot = {
    server_id: 'alpha-1',
    selected_roots: [{ mount_id: 'data', capacity_id: 'dev-8-1', storage_media: 'ssd' }],
    mounts: [{ mount_id: 'data', path: '/data', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40 }],
  };
  const bootstrap = await viewer.loadBootstrapDataWith({
    loadSession: async () => ({ authenticated: false, can_rescan: false, csrf_token: '' }),
    loadServerSummaries: async () => ({
      data_mode: 'inventory',
      servers: [{ id: 'alpha-1', display_name: 'alpha', order: 1, mount_count: 1, overview_snapshot: overviewSnapshot }],
    }),
    loadOrderedSnapshotsForOverview: async () => { fullSnapshotLoads += 1; return []; },
    loadServerSnapshot: async () => ({})
  });

  assert.strictEqual(bootstrap.snapshots.length, 1, 'embedded capacity snapshot must be ready for the first meaningful overview render');
  assert.strictEqual(bootstrap.snapshots[0].snapshot, overviewSnapshot);
  assert.strictEqual(bootstrap.snapshots[0].overviewOnly, true, 'embedded summaries must be marked as overview-only detail data');
  const hydrated = await bootstrap.startSnapshotLoading(() => {});
  assert.strictEqual(fullSnapshotLoads, 0, 'overview must not download a full server snapshot when embedded capacity data exists');
  assert(Array.isArray(hydrated) && hydrated.length === 0);
}

async function testOverviewOnlySnapshotNeverSatisfiesDetailLoading() {
  const viewer = loadViewer();
  let detailLoads = 0;
  viewer.rememberBootstrap({
    mode: 'api', dataMode: 'inventory', session: {},
    summaries: [{ id: 'alpha-1', display_name: 'alpha', order: 1, mount_count: 1 }],
    snapshots: [{ id: 'alpha-1', overviewOnly: true, snapshot: { server_id: 'alpha-1', mounts: [] }, error: null }],
  });
  viewer.loadSnapshotForCurrentSource = async () => {
    detailLoads += 1;
    return { server_id: 'alpha-1', hostname: 'alpha-full', selected_roots: [], mounts: [], users: [], top_files: [], stale: [] };
  };
  viewer.applyRouteState({ serverId: 'alpha-1', tab: 'treemap' }, { skipHistory: true, skipDataLoad: true });
  await viewer.ensureDetailLoaded('alpha-1', false);
  assert.strictEqual(detailLoads, 1, 'detail route must fetch the full snapshot instead of reusing overview-only capacity data');
  assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'alpha-full');
}

function testOverviewHydrationCannotOverwriteANewerDetailRequest() {
  const app = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
  const start = app.indexOf('function startOverviewSnapshotHydration');
  const end = app.indexOf('\nasync function loadSnapshotForCurrentSource', start);
  assert(start >= 0 && end > start, 'overview hydration helper must exist');
  const body = app.slice(start, end);
  assert(/const detailVersionsAtStart = new Map\(detailRequestVersions\);/.test(body), 'background hydration must capture per-server detail versions before requests start');
  const guard = body.indexOf('(detailRequestVersions.get(entry.id) || 0) !== (detailVersionsAtStart.get(entry.id) || 0)');
  const cacheWrite = body.indexOf('snapshotCache.set(entry.id, entry.snapshot)');
  assert(guard >= 0 && guard < cacheWrite, 'a newer detail request must invalidate stale background snapshot writes');
}

function testSampleMarkerAndCompactOverviewOmitsAggregateSurface() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.rememberBootstrap, 'function', 'bootstrap state setter must be exposed');
  assert.strictEqual(typeof viewer.getOverviewModeDebugState, 'function', 'overview mode debug getter must be exposed');

  const marker = viewer.document.getElementById('sampleDataMarker');
  viewer.rememberBootstrap({ mode: 'static', dataMode: 'sample', session: {}, summaries: [], snapshots: [] });
  assert.strictEqual(marker.hidden, false, 'sample marker must be visible only when explicit dataMode is sample');
  assert.strictEqual(viewer.getOverviewModeDebugState().dataMode, 'sample', 'rememberBootstrap must retain sample dataMode metadata');
  viewer.rememberBootstrap({ mode: 'api', dataMode: 'inventory', session: {}, summaries: [], snapshots: [] });
  assert.strictEqual(marker.hidden, true, 'sample marker must be hidden in inventory/live mode');
  assert.strictEqual(viewer.getOverviewModeDebugState().dataMode, 'inventory', 'rememberBootstrap must retain inventory dataMode metadata');

  const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  const app = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
  assert(!html.includes('id="overviewAggregate"'), 'compact overview must not render a page aggregate container');
  assert(!/renderOverviewAggregate\s*\(/.test(app), 'compact overview render path must not invoke renderOverviewAggregate');
}

function testMountCentricOverviewDomFieldsAndStableNavigation() {
  const viewer = loadViewer();
  const row = viewer.buildOverviewServer(
    { id: 'hinton', display_name: 'hinton', order: 99, mount_count: 2 },
    {
      server_id: 'hinton',
      selected_roots: [
        { mount_id: 'home', capacity_id: 'dev-8-1', storage_media: 'ssd' },
        { mount_id: 'data', capacity_id: 'dev-8-16', block_media: 'hdd' },
      ],
      mounts: [
        { mount_id: 'home', path: '/home', df_total: 1000 * 1024 ** 3, df_used: 400 * 1024 ** 3, df_avail: 600 * 1024 ** 3, df_use_pct: 40 },
        { mount_id: 'data', path: '/data', df_total: 2000 * 1024 ** 3, df_used: 1800 * 1024 ** 3, df_avail: 200 * 1024 ** 3, df_use_pct: 90 },
      ],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );
  const later = viewer.buildOverviewServer(
    { id: 'atlas', display_name: 'atlas', order: 1, mount_count: 1 },
    {
      server_id: 'atlas',
      selected_roots: [{ mount_id: 'archive', capacity_id: 'dev-9-1', storage_media: 'mixed' }],
      mounts: [{ mount_id: 'archive', path: '/archive', df_total: 3000 * 1024 ** 3, df_used: 300 * 1024 ** 3, df_avail: 2700 * 1024 ** 3, df_use_pct: 10 }],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );
  const opened = [];
  const list = viewer.document.getElementById('overviewList');
  viewer.renderOverviewList(list, [row, later], { onOpenServer: serverId => opened.push(serverId) });

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
  assert.strictEqual(textTree(findByClass(first, 'overview-mount-free')[0]), '여유 600 GB', 'healthy mount free text must not append redundant normal status text');
  assert(accessibleRowText.includes('/home') && accessibleRowText.includes('SSD') && accessibleRowText.includes('40%') && accessibleRowText.includes('여유 600 GB'), 'mount path, media, percent, and free information must remain accessible as descendant text');
  assert(!accessibleRowText.includes('400 GB / 1000 GB'), 'compact mount strip must omit used/total text');

  hintonButton.onclick();
  assert.deepStrictEqual(opened, ['hinton'], 'click navigation to server detail must be preserved');
}


function testMountStatusTextAppearsOnlyForExceptionalPressure() {
  const viewer = loadViewer();
  const row = viewer.buildOverviewServer(
    { id: 'pressure-1', display_name: 'pressure', mount_count: 3, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    {
      server_id: 'pressure-1',
      selected_roots: [
        { mount_id: 'healthy', capacity_id: 'dev-8-1', storage_media: 'ssd' },
        { mount_id: 'warn', capacity_id: 'dev-8-2', storage_media: 'ssd' },
        { mount_id: 'crit', capacity_id: 'dev-8-3', storage_media: 'hdd' },
      ],
      mounts: [
        { mount_id: 'healthy', path: '/healthy', df_total: 1000 * 1024 ** 3, df_used: 400 * 1024 ** 3, df_avail: 600 * 1024 ** 3, df_use_pct: 40 },
        { mount_id: 'warn', path: '/warn', df_total: 1000 * 1024 ** 3, df_used: 810 * 1024 ** 3, df_avail: 190 * 1024 ** 3, df_use_pct: 81 },
        { mount_id: 'crit', path: '/crit', df_total: 1000 * 1024 ** 3, df_used: 930 * 1024 ** 3, df_avail: 70 * 1024 ** 3, df_use_pct: 93 },
      ],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );
  const list = viewer.document.getElementById('overviewList');
  viewer.renderOverviewList(list, [row], { onOpenServer() {} });
  const cells = findByClass(list.children[0].children[0], 'overview-mount');
  const freeTexts = cells.map(cell => textTree(findByClass(cell, 'overview-mount-free')[0]));
  assert.strictEqual(freeTexts[0], '여유 600 GB', 'healthy mount free text must not append redundant normal text');
  assert.strictEqual(freeTexts[1], '여유 190 GB', 'warning mount free text must remain a clean numeric field');
  assert.strictEqual(freeTexts[2], '여유 70.0 GB', 'critical mount free text must remain a clean numeric field');
  const footerText = textTree(findByClass(list.children[0].children[0], 'overview-card-footer')[0]);
  assert(footerText.includes('주의 1') && footerText.includes('위험 1'), 'non-color pressure labels must be consolidated in the card footer');
  assert(findByClass(list.children[0].children[0], 'overview-mount')[1].getAttribute('aria-label').includes('주의'), 'warning mount must expose its exact state without relying on color');
  assert(findByClass(list.children[0].children[0], 'overview-mount')[2].getAttribute('aria-label').includes('위험'), 'critical mount must expose its exact state without relying on color');
}

function testServerHeaderMetaIsActionableMountCountOnly() {
  const viewer = loadViewer();
  const list = viewer.document.getElementById('overviewList');
  const row = viewer.buildOverviewServer(
    { id: 'alpha-1', display_name: 'alpha', mount_count: 2 },
    {
      server_id: 'alpha-1',
      selected_roots: [
        { mount_id: 'data', capacity_id: 'dev-8-16', storage_media: 'hdd' },
        { mount_id: 'data-bind', capacity_id: 'dev-8-16', storage_media: 'hdd' },
      ],
      mounts: [
        { mount_id: 'data', path: '/data', df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
        { mount_id: 'data-bind', path: '/data-bind', df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
      ],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );
  assert.strictEqual(row.aggregate.availableLabel, '500 B', 'model may keep aggregate capacity for non-header calculations');
  viewer.renderOverviewList(list, [row], { onOpenServer() {} });
  const button = list.children[0].children[0];
  const metaText = textTree(findByClass(button, 'overview-meta')[0]);
  assert.strictEqual(metaText, '2개 마운트', 'server header metadata must be actionable mount count only');
  assert(!metaText.includes(' free'), 'server header metadata must not include total available/free aggregate text');
  assert(!metaText.includes('500 B'), 'server header metadata must not expose aggregate capacity');
  assert.strictEqual(findByClass(button, 'overview-server-subtotal').length, 0, 'compact server rows must not render subtotal labels');
}

function testUnknownMountCapacityDomStaysNeutralAndAccessible() {
  const viewer = loadViewer();
  const row = viewer.buildOverviewServer(
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
        { mount_id: 'missing-free', path: '/missing-free', df_total: 2048, df_used: 1024, df_use_pct: 95 },
        { mount_id: 'invalid-cap', path: '/invalid-cap', df_total: '4096', df_used: -1, df_avail: Infinity, df_use_pct: 80 },
      ],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );
  assert.strictEqual(row.primaryStatus.code, 'normal', 'unknown-only mount pressure must not promote the server row to warning or critical');
  const list = viewer.document.getElementById('overviewList');
  viewer.renderOverviewList(list, [row], { onOpenServer() {} });
  const button = list.children[0].children[0];
  assert.strictEqual(button.getAttribute('data-primary-status'), 'normal', 'rendered primary status must remain normal for unknown-only pressure');
  const text = textTree(button);
  assert(text.includes('/missing-free') && text.includes('Unknown'), 'unknown media/path must remain visible and accessible');
  assert(text.includes('여유 미확인'), 'unknown free capacity must render honest Korean copy');
  assert(!text.includes('— / —'), 'compact mount strip must omit used/total capacity text');
  assert(!text.includes('여유 0 B'), 'unknown free capacity must never render as 0 B free');
  assert.strictEqual(button.getAttribute('aria-label'), undefined, 'unknown mount details must not be hidden behind a row aria-label');
}


async function testDetailCapacityUsesCompactRowsAndFiltersBootMounts() {
  const viewer = loadViewer();
  viewer.rememberBootstrap({
    mode: 'api',
    session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: [
      { id: 'alpha-1', display_name: 'alpha', mount_count: 3, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    ],
    snapshots: [],
    dataMode: 'inventory',
  });
  viewer.loadSnapshotForCurrentSource = async () => ({
    server_id: 'alpha-1',
    hostname: 'alpha-host',
    scanner_version: '1.0',
    run_as_root: true,
    users: [],
    top_files: [],
    stale: [],
    mounts: [
      { mount_id: 'boot', path: '/boot', fstype: 'vfat', storage_media: 'ssd', df_total: 1024, df_used: 512, df_avail: 512, df_use_pct: 50 },
      { mount_id: 'root', path: '/', fstype: 'ext4', storage_media: 'ssd', df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40 },
      { mount_id: 'data', path: '/data', fstype: 'xfs', storage_media: 'hdd', df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
    ],
  });
  viewer.navigateToServer('alpha-1', { skipHistory: true, skipDataLoad: true });
  await viewer.ensureDetailLoaded('alpha-1');
  await flushPromises();

  const caps = viewer.document.getElementById('caps');
  assert(caps.className.split(/\s+/).includes('detail-capacity-rail'), 'detail capacity must use compact rail class');
  assert.strictEqual(caps.children.length, 2, 'boot mounts must be omitted from detail capacity rows');
  assert(caps.children.every(child => child.className.split(/\s+/).includes('detail-capacity-row')), 'each detail mount must render as a compact capacity row');
  const html = caps.children.map(child => child.innerHTML).join('\n');
  assert(!html.includes('/boot'), 'boot mount must be absent from legacy detail capacity snapshots');
  assert(html.includes('cap-path') && html.includes('/') && html.includes('/data'), 'mount paths and existing cap-path selector must be preserved');
  assert(html.includes('cap-fs') && html.includes('ext4') && html.includes('xfs'), 'filesystem values must be preserved exactly');
  assert(html.includes('cap-media') && html.includes('ssd') && html.includes('hdd'), 'media values must be exposed in the compact row');
  assert(html.includes('400 B') && html.includes('1000 B') && html.includes('1.46 KB') && html.includes('1.95 KB'), 'used/total values must be preserved exactly through existing formatting');
  assert(html.includes('40') && html.includes('75'), 'percentage values must be preserved');
  assert(html.includes('600 B') && html.includes('500 B'), 'free capacity values must be preserved');
  assert(html.includes('cap-bar') && html.includes('cap-fill'), 'thin utilization bar IDs/classes must remain available');
  assert(!caps.children.some(child => child.className.split(/\s+/).includes('cap')), 'detail capacity must no longer use the large capacity-card grid contract');

  const state = viewer.getCurrentDetailDebugState();
  assert.deepStrictEqual(state.data.mounts.map(m => m.path), ['/', '/data'], 'detail DATA.mounts must be filtered before downstream mount selectors derive paths');
}


async function testDetailNormalizationFiltersBootEverywhereAndRecomputesUsers() {
  const viewer = loadViewer();
  viewer.rememberBootstrap({
    mode: 'api',
    session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: [
      { id: 'alpha-1', display_name: 'alpha', mount_count: 4, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    ],
    snapshots: [],
    dataMode: 'inventory',
  });
  viewer.loadSnapshotForCurrentSource = async () => ({
    server_id: 'alpha-1',
    hostname: 'alpha-host',
    scanner_version: '1.0',
    run_as_root: true,
    mounts: [
      { mount_id: 'boot', path: '/boot', fstype: 'vfat', df_total: 1000, df_used: 900, df_avail: 100, df_use_pct: 90 },
      { mount_id: 'efi', path: '/boot/efi', fstype: 'vfat', df_total: 1000, df_used: 800, df_avail: 200, df_use_pct: 80 },
      { mount_id: 'data', path: '/data', fstype: 'xfs', df_total: 5000, df_used: 1200, df_avail: 3800, df_use_pct: 24 },
      { mount_id: 'bootloader', path: '/bootloader', fstype: 'xfs', df_total: 2000, df_used: 300, df_avail: 1700, df_use_pct: 15 },
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
  viewer.navigateToServer('alpha-1', { skipHistory: true, skipDataLoad: true });
  await viewer.ensureDetailLoaded('alpha-1');
  await flushPromises();

  const state = viewer.getCurrentDetailDebugState().data;
  assert.deepStrictEqual(state.mounts.map(m => m.path), ['/data', '/bootloader'], 'central detail normalization must remove /boot mounts while preserving non-boot prefix paths');
  assert.deepStrictEqual(state.top_files.map(f => f.path), ['/data/model.bin', '/bootloader/readme.txt'], 'top files must not leak /boot or /boot descendants');
  assert.deepStrictEqual(state.stale.map(f => f.path), ['/data/old.bin'], 'stale files must not leak /boot or /boot descendants');
  const alice = state.users.find(u => u.uid === 1000);
  assert.deepStrictEqual(Object.keys(alice.by_mount).sort(), ['/bootloader', '/data'], 'user by_mount maps must remove /boot keys but keep non-boot prefix paths');
  assert.strictEqual(alice.bytes, 1500, 'All-user bytes must be recomputed from actionable mount paths rather than stale user bytes');
  assert.strictEqual(state.users.some(u => u.uid === 1001), false, 'boot-only users must disappear after actionable-byte normalization');
}

async function testDetailCapacityUnknownNumbersRenderNeutralDashes() {
  const viewer = loadViewer();
  viewer.rememberBootstrap({
    mode: 'api',
    session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: [
      { id: 'alpha-1', display_name: 'alpha', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    ],
    snapshots: [],
    dataMode: 'inventory',
  });
  viewer.loadSnapshotForCurrentSource = async () => ({
    server_id: 'alpha-1', hostname: 'alpha-host', scanner_version: '1.0', run_as_root: true,
    users: [], top_files: [], stale: [],
    mounts: [{ mount_id: 'mystery', path: '/mystery', fstype: 'xfs', storage_media: 'unknown' }],
  });
  viewer.navigateToServer('alpha-1', { skipHistory: true, skipDataLoad: true });
  await viewer.ensureDetailLoaded('alpha-1');
  await flushPromises();

  const row = viewer.document.getElementById('caps').children[0];
  const html = row.innerHTML;
  assert.strictEqual(row.getAttribute('data-pressure'), 'unknown', 'missing detail capacity metrics must use neutral unknown styling');
  assert(html.includes('<span class="figure">—</span> used / <span class="figure">—</span>'), 'missing used/total must render em dashes');
  assert(html.includes('<div class="cap-pct">—</div>'), 'missing percentage must render an em dash instead of invented 0%');
  assert(html.includes('<span class="figure">—</span> free'), 'missing available bytes must render an em dash');
  assert(!html.includes('0<span>%</span>') && !html.includes('var(--ok)'), 'unknown detail capacity must not invent healthy 0% styling');
}

async function testZeroActionableMountsUseExactKoreanEmptyCopy() {
  const viewer = loadViewer();
  const snapshot = {
    server_id: 'boot-only',
    selected_roots: [{ mount_id: 'boot', capacity_id: 'dev-8-1', storage_media: 'ssd' }],
    mounts: [{ mount_id: 'boot', path: '/boot', df_total: 1000, df_used: 500, df_avail: 500, df_use_pct: 50 }],
  };
  const row = viewer.buildOverviewServer({ id: 'boot-only', display_name: 'boot-only', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null }, snapshot, viewer.DEFAULT_CAPACITY_THRESHOLDS);
  const list = viewer.document.getElementById('overviewList');
  viewer.renderOverviewList(list, [row], { onOpenServer() {} });
  assert.strictEqual(textTree(list), 'boot-only0개 마운트표시할 데이터 마운트 없음', 'overview must use the exact Korean empty-mount copy');

  viewer.rememberBootstrap({
    mode: 'api', session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: [{ id: 'boot-only', display_name: 'boot-only', mount_count: 1, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null }],
    snapshots: [], dataMode: 'inventory',
  });
  viewer.loadSnapshotForCurrentSource = async () => Object.assign({ hostname: 'boot-only', scanner_version: '1.0', run_as_root: true, users: [], top_files: [], stale: [] }, snapshot);
  viewer.navigateToServer('boot-only', { skipHistory: true, skipDataLoad: true });
  await viewer.ensureDetailLoaded('boot-only');
  await flushPromises();
  assert.strictEqual(textTree(viewer.document.getElementById('caps')), '표시할 데이터 마운트 없음', 'detail must use the exact Korean empty-mount copy');
}


function testDetailCapacityResponsiveCssContract() {
  const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');
  assert(/\.detail-capacity-row\b[\s\S]*min-width:\s*0/.test(css), 'detail capacity rows must allow grid children to shrink without forcing horizontal overflow');
  assert(/\.cap-main\b[\s\S]*flex-wrap:\s*wrap/.test(css), 'detail capacity path/filesystem/media group must wrap instead of clipping on narrow screens');
  assert(/\.cap-bar\b[\s\S]*min-width:\s*[1-9][0-9]*px/.test(css), 'detail utilization bar must keep a visible minimum width when rows collapse');
  assert(/@media\s*\(max-width:\s*760px\)[\s\S]*\.detail-capacity-row\b[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/.test(css), '760px detail capacity rows must switch to a shrinkable two-column layout');
  assert(/@media\s*\(max-width:\s*760px\)[\s\S]*\.detail-capacity-row\s*>\s*\*\s*\{[^}]*min-width:\s*0/.test(css), '760px detail capacity row children must explicitly set min-width:0');
  assert(/@media\s*\(max-width:\s*760px\)[\s\S]*\.cap-bar\b[\s\S]*grid-column:\s*1\s*\/\s*-1/.test(css), '760px detail utilization bar must span the full row to remain visible');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.caps\.detail-capacity-rail\b[\s\S]*padding:\s*8px\s+14px/.test(css), '390px detail capacity rail must reduce side padding to avoid horizontal overflow');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.caps\.detail-capacity-rail\b[\s\S]*grid-auto-flow:\s*column[\s\S]*grid-auto-columns:\s*minmax\(220px,\s*78vw\)/.test(css), '390px detail capacity cards must use one horizontal rail so the treemap remains near the top');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.detail-capacity-row\b[\s\S]*grid-template-columns:\s*1fr/.test(css), '390px detail capacity rows must stack into one column');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.detail-capacity-row\b[\s\S]*grid-template-areas:\s*"main"\s*"pct"\s*"sub"\s*"free"\s*"bar"/.test(css), '390px detail capacity rows must override every named area into one column');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.cap-pct\b[\s\S]*text-align:\s*left/.test(css), 'stacked detail percentage must remain readable as normal text');
  assert(/@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*\.cap-fill\b[\s\S]*transition:\s*none\s*!important/.test(css), 'detail capacity fill animation must be explicitly disabled for reduced motion');
}

function testDetailHeaderMobileCssContract() {
  const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*body\[data-shell-mode=['"]detail['"]\]\s+\.head-row\b[\s\S]*display:\s*grid[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto\s+auto/.test(css), '390px detail header must use a bounded three-column control row');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*body\[data-shell-mode=['"]detail['"]\]\s+\.brand-shell\b[\s\S]*display:\s*none/.test(css), '390px detail header must omit the redundant product title');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*body\[data-shell-mode=['"]detail['"]\]\s+#lastUpd\b[\s\S]*display:\s*none/.test(css), '390px detail header must omit the redundant last-updated label');
}


function testOverviewMonitorCardHierarchyContract() {
  const viewer = loadViewer();
  const list = viewer.document.getElementById('overviewList');
  const row = viewer.buildOverviewServer(
    { id: 'dense-1', display_name: 'dense', mount_count: 3, snapshot_availability: 'available', freshness: 'fresh', latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync', active_job: null },
    {
      server_id: 'dense-1',
      selected_roots: [
        { mount_id: 'alpha', capacity_id: 'dev-8-1', storage_media: 'ssd' },
        { mount_id: 'beta', capacity_id: 'dev-8-2', storage_media: 'hdd' },
        { mount_id: 'gamma', capacity_id: 'dev-8-3', storage_media: 'mixed' },
      ],
      mounts: [
        { mount_id: 'alpha', path: '/alpha', df_total: 1000 * 1024 ** 3, df_used: 200 * 1024 ** 3, df_avail: 800 * 1024 ** 3, df_use_pct: 20 },
        { mount_id: 'beta', path: '/beta', df_total: 1000 * 1024 ** 3, df_used: 850 * 1024 ** 3, df_avail: 150 * 1024 ** 3, df_use_pct: 85 },
        { mount_id: 'gamma', path: '/gamma', df_total: 1000 * 1024 ** 3, df_used: 940 * 1024 ** 3, df_avail: 60 * 1024 ** 3, df_use_pct: 94 },
      ],
    },
    viewer.DEFAULT_CAPACITY_THRESHOLDS,
  );
  viewer.renderOverviewList(list, [row], { onOpenServer() {} });
  const card = list.children[0].children[0];
  const text = textTree(card);
  assert.strictEqual(card.tagName, 'A', 'each server must render as a native monitor-card link');
  assert.strictEqual(findByClass(card, 'overview-status-dot').length, 1, 'server state must use one compact status dot');
  assert.strictEqual(findByClass(card, 'overview-badge').length, 0, 'server cards must not lead with a large warning pill');
  assert(!text.includes('정상'), 'overview must not render healthy status copy in server or mount text');
  assert(!text.includes('전체') && !text.includes('집계'), 'overview rows must not include page-level or aggregate capacity copy');
  assert.strictEqual(findByClass(card, 'overview-card-header').length, 1, 'server identity must use the same compact card header anatomy as GPU Monitor');
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
}

function testOverviewMasonryPreservesOrderAcrossResponsiveColumns() {
  const viewer = loadViewer();
  const heights = [316, 250, 170, 250, 170, 210, 120];
  const items = heights.map(height => ({
    style: {},
    firstElementChild: { getBoundingClientRect: () => ({ height }) },
    getBoundingClientRect: () => ({ height }),
  }));
  const container = { children: items, offsetWidth: 1232 };
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '401px 401px 401px' });
  viewer.layoutOverviewMasonry(container);
  assert.deepStrictEqual(items.map(item => item.style.gridColumn), ['1', '2', '3', '1', '2', '3', '1'], 'three-column overview layout must assign cards deterministically by DOM order modulo column count');
  const threeColumnStarts = items.map(item => Number(item.style.gridRow.split('/')[0].trim()));
  assert.deepStrictEqual(threeColumnStarts, [1, 1, 1, 23, 19, 14, 41], 'three-column starts must stack vertically within each deterministic column');

  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '609px 609px' });
  viewer.layoutOverviewMasonry(container);
  assert.deepStrictEqual(items.map(item => item.style.gridColumn), ['1', '2', '1', '2', '1', '2', '1'], 'two-column overview layout must recompute deterministic odd/even columns from cleared placement');
  const twoColumnStarts = items.map(item => Number(item.style.gridRow.split('/')[0].trim()));
  assert.deepStrictEqual(twoColumnStarts, [1, 1, 23, 19, 36, 37, 49], 'two-column starts must stack vertically within each deterministic column');

  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '370px' });
  viewer.layoutOverviewMasonry(container);
  assert.deepStrictEqual(items.map(item => item.style.gridColumn), ['1', '1', '1', '1', '1', '1', '1'], 'one-column overview layout must place every card in column 1');
  const oneColumnStarts = items.map(item => Number(item.style.gridRow.split('/')[0].trim()));
  assert(oneColumnStarts.every((start, index) => index === 0 || start > oneColumnStarts[index - 1]), 'mobile cards must keep strict server order without overlap');
}



function makeOverviewLayoutItem(height, rects) {
  let rectIndex = 0;
  return {
    style: {},
    firstElementChild: { getBoundingClientRect: () => ({ height }) },
    getBoundingClientRect: () => rects[Math.min(rectIndex++, rects.length - 1)],
    offsetWidth: 10,
  };
}

function testOverviewLayoutClearsStaleInlinePlacementAndMotion() {
  const viewer = loadViewer();
  const items = [
    makeOverviewLayoutItem(120, [{ left: 0, top: 0 }, { left: 0, top: 0 }]),
    makeOverviewLayoutItem(120, [{ left: 0, top: 0 }, { left: 354, top: 0 }]),
  ];
  items[0].style.gridColumn = '99';
  items[0].style.gridRow = '99 / span 99';
  items[0].style.transform = 'translate(9px, 9px)';
  items[0].style.transition = 'transform 280ms cubic-bezier(.22,1,.36,1)';
  const container = { children: items, offsetWidth: 714, __overviewColumnCount: 2 };
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '350px 350px' });

  viewer.layoutOverviewMasonry(container);

  assert.deepStrictEqual(items.map(item => item.style.gridColumn), ['1', '2'], 'layout must recompute grid columns from cleared stale placement');
  assert.strictEqual(items[0].style.gridRow, '1 / span 9', 'layout must recompute grid rows from cleared stale placement');
  assert.strictEqual(items[0].style.transform, '', 'same-column-count relayout must clear stale FLIP transform leftovers');
  assert.strictEqual(items[0].style.transition, '', 'same-column-count relayout must clear stale FLIP transition leftovers');
}

function testOverviewFlipRunsOnlyOnColumnCountChangeAndSkipsReducedMotion() {
  const viewer = loadViewer();
  viewer.setTimeout = () => undefined;
  viewer.window.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  const moving = makeOverviewLayoutItem(120, [{ left: 354, top: 0 }, { left: 0, top: 150 }]);
  const container = { children: [moving], offsetWidth: 640, __overviewColumnCount: 2 };
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '640px' });

  viewer.layoutOverviewMasonry(container);
  assert.strictEqual(container.__overviewColumnCount, 1, 'layout must remember the newly active column count');
  assert.strictEqual(moving.style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'column-count changes should use the approved restrained FLIP timing');
  assert.strictEqual(moving.style.transform, 'translate(0, 0)', 'column-count changes should animate back to natural placement');

  moving.style.transform = '';
  moving.style.transition = '';
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '620px' });
  viewer.layoutOverviewMasonry(container);
  assert.strictEqual(moving.style.transform, '', 'continuous width resize at the same column count must not run FLIP');
  assert.strictEqual(moving.style.transition, '', 'continuous width resize at the same column count must not add FLIP transition');

  const reduced = makeOverviewLayoutItem(120, [{ left: 0, top: 0 }, { left: 354, top: 0 }]);
  const reducedContainer = { children: [reduced], offsetWidth: 714, __overviewColumnCount: 1 };
  viewer.window.matchMedia = () => ({ matches: true, addEventListener() {}, addListener() {} });
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '350px 350px' });
  viewer.layoutOverviewMasonry(reducedContainer);
  assert.strictEqual(reducedContainer.__overviewColumnCount, 2, 'reduced-motion relayout still updates the active column count');
  assert.strictEqual(reduced.style.transform, '', 'reduced motion must skip FLIP transforms');
  assert.strictEqual(reduced.style.transition, '', 'reduced motion must skip FLIP transitions');
}


function testOverviewFlipCapturesAllFirstRectsBeforeClearingPlacement() {
  const viewer = loadViewer();
  viewer.setTimeout = () => undefined;
  const animationFrames = [];
  viewer.requestAnimationFrame = (fn) => { animationFrames.push(fn); return animationFrames.length; };
  viewer.window.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  const items = [];
  const firstPositions = [
    { left: 0, top: 0 },
    { left: 354, top: 0 },
    { left: 0, top: 150 },
  ];
  const finalPositions = [
    { left: 0, top: 0 },
    { left: 0, top: 134 },
    { left: 0, top: 268 },
  ];
  for (let index = 0; index < 3; index += 1) {
    items[index] = {
      style: { gridColumn: index === 1 ? '2' : '1', gridRow: String(index + 1) + ' / span 9' },
      firstElementChild: { getBoundingClientRect: () => ({ height: 120 }) },
      getBoundingClientRect() {
        const originalTwoColumnPlacementStillIntact = items[0].style.gridColumn === '1' && items[1].style.gridColumn === '2' && items[2].style.gridColumn === '1';
        return originalTwoColumnPlacementStillIntact ? firstPositions[index] : finalPositions[index];
      },
      offsetWidth: 10,
    };
  }
  const container = { children: items, offsetWidth: 640, __overviewColumnCount: 2 };
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '640px' });

  viewer.layoutOverviewMasonry(container);

  assert.strictEqual(items[1].style.transition, 'none', 'later items must be prepared for FLIP before the animation frame runs');
  assert.strictEqual(items[1].style.transform, 'translate(354px, -134px)', 'later item FLIP delta must be computed from the pre-clear two-column rect, not the post-clear one-column rect');
  assert.strictEqual(items[2].style.transform, 'translate(0px, -118px)', 'all item first rects must be captured before any stale placement is cleared');
  for (const frame of animationFrames) frame();
  assert.strictEqual(items[1].style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'later items must animate during a real column-count change');
  assert.strictEqual(items[1].style.transform, 'translate(0, 0)', 'later items must complete FLIP from their previous two-column visual position');
}


function testOverviewFlipIgnoresStaleCallbacksAfterRapidRelayout() {
  const viewer = loadViewer();
  const animationFrames = [];
  const timers = [];
  viewer.requestAnimationFrame = (fn) => { animationFrames.push(fn); return animationFrames.length; };
  viewer.cancelAnimationFrame = (id) => { animationFrames[id - 1] = null; };
  viewer.setTimeout = (fn) => { timers.push(fn); return timers.length; };
  viewer.clearTimeout = (id) => { timers[id - 1] = null; };
  viewer.window.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  let positions = [{ left: 354, top: 0 }, { left: 0, top: 134 }];
  const item = {
    style: {},
    firstElementChild: { getBoundingClientRect: () => ({ height: 120 }) },
    getBoundingClientRect: () => positions.shift() || { left: 0, top: 134 },
    offsetWidth: 10,
  };
  const container = { children: [item], offsetWidth: 640, __overviewColumnCount: 2 };
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '640px' });

  viewer.layoutOverviewMasonry(container);
  assert.strictEqual(item.style.transform, 'translate(354px, -134px)', 'first relayout prepares FLIP before RAF runs');

  item.style.transform = 'translate(18px, 0px)';
  item.style.transition = 'transform 280ms cubic-bezier(.22,1,.36,1)';
  positions = [{ left: 0, top: 134 }];
  viewer.layoutOverviewMasonry(container);
  assert.strictEqual(item.style.transform, '', 'newer same-column relayout clears stale transform before old RAF can run');
  assert.strictEqual(item.style.transition, '', 'newer same-column relayout clears stale transition before old RAF can run');

  for (const frame of animationFrames) if (frame) frame();
  assert.strictEqual(item.style.transform, '', 'stale RAF from the older column-change relayout must not restore transform');
  assert.strictEqual(item.style.transition, '', 'stale RAF from the older column-change relayout must not restore transition');

  positions = [{ left: 0, top: 134 }, { left: 354, top: 0 }];
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '350px 350px' });
  let frameStart = animationFrames.length;
  viewer.layoutOverviewMasonry(container);
  for (const frame of animationFrames.slice(frameStart)) if (frame) frame();
  const oldCleanup = timers[timers.length - 1];
  assert.strictEqual(item.style.transform, 'translate(0, 0)', 'older animation should schedule cleanup after its RAF runs');
  assert.strictEqual(item.style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'older animation should use the approved transition before cleanup');

  positions = [{ left: 354, top: 0 }, { left: 0, top: 134 }];
  viewer.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '640px' });
  frameStart = animationFrames.length;
  viewer.layoutOverviewMasonry(container);
  for (const frame of animationFrames.slice(frameStart)) if (frame) frame();
  assert.strictEqual(item.style.transform, 'translate(0, 0)', 'newer animation should be allowed to own the transform');
  assert.strictEqual(item.style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'newer animation should be allowed to own the transition');

  if (oldCleanup) oldCleanup();
  assert.strictEqual(item.style.transform, 'translate(0, 0)', 'old cleanup timer must not clear a newer animation transform');
  assert.strictEqual(item.style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'old cleanup timer must not clear a newer animation transition');
}

function testOverviewResizeFallbackSchedulesOnlyOverviewRelayout() {
  const app = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
  assert(/window\.addEventListener\("resize", \(\) => \{[\s\S]*setTimeout\(\(\) => \{[\s\S]*document\.body && document\.body\.dataset\.shellMode === "overview"[\s\S]*scheduleOverviewMasonry\(document\.getElementById\("overviewList"\)\)/.test(app), 'window resize fallback must throttle and schedule overview layout only while the overview shell is active');
}

function cssBlock(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(escaped + "\\s*\\{([\\s\\S]*?)\\}"));
  assert(match, selector + ' token block must exist');
  return match[1];
}

function cssVar(block, name) {
  const match = block.match(new RegExp('--' + name + ':\\s*([^;]+);'));
  assert(match, '--' + name + ' must be present');
  return match[1].trim();
}

function testApprovedCleanThemeTokenContract() {
  const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');
  const dark = cssBlock(css, 'html.dark');
  const light = cssBlock(css, 'html.light');
  const expectedDark = {
    bg: '#090b0f',
    surface: '#13161b',
    surface2: '#181b1f',
    separator: '#26292e',
    text: '#f0f2f4',
    text2: '#8f9aa4',
    accent: '#3a8cff',
    ok: '#00b793',
    warn: '#ff7527',
    crit: '#ff515a',
  };
  const expectedLight = {
    bg: '#f4f5f7',
    surface: '#ffffff',
    surface2: '#eceff1',
    separator: '#dbdee2',
    text: '#0c121a',
    text2: '#565e69',
    accent: '#297cef',
    ok: '#00a381',
    warn: '#f3680f',
    crit: '#ee343b',
  };
  for (const [name, value] of Object.entries(expectedDark)) {
    assert.strictEqual(cssVar(dark, name), value, 'dark Clean token --' + name + ' must match the approved contract exactly');
  }
  for (const [name, value] of Object.entries(expectedLight)) {
    assert.strictEqual(cssVar(light, name), value, 'light Clean token --' + name + ' must match the approved contract exactly');
  }
}

function testSuccessfulOverviewSuppressesServerCountLiveLead() {
  const viewer = loadViewer();
  const status = viewer.document.getElementById('overviewStatus');
  status.textContent = 'Loading servers…';
  status.hidden = false;
  viewer.rememberBootstrap({
    mode: 'api',
    dataMode: 'inventory',
    session: { authenticated: true, can_rescan: false, csrf_token: 'csrf' },
    summaries: Array.from({ length: 7 }, (_, index) => ({
      id: 'server-' + index,
      display_name: 'server-' + index,
      order: index,
      mount_count: 0,
      snapshot_availability: 'available',
      freshness: 'fresh',
      latest_pull_status: 'succeeded',
      latest_scan_result: 'complete',
      configuration_sync: 'in_sync',
      active_job: null,
    })),
    snapshots: Array.from({ length: 7 }, (_, index) => ({
      id: 'server-' + index,
      snapshot: { server_id: 'server-' + index, mounts: [] },
      error: null,
    })),
  });
  viewer.renderOverview();
  const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  assert(/id="overviewStatus"[^>]*aria-live="polite"/.test(html), 'overview status must preserve its aria-live loading/error announcement channel');
  assert.strictEqual(status.textContent, '', 'successful overview must not announce or display a redundant server-count lead');
  assert.strictEqual(status.hidden, true, 'successful overview status lead must be hidden after load');
  assert(!textTree(viewer.document.getElementById('overviewView')).includes('7 servers'), 'successful overview view must not visibly contain the old server-count lead');
}

function testMountCentricResponsiveCssContract() {
  const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');
  assert(/--overview-max-width:\s*1440px/.test(css), 'overview CSS must expose a semantic max-width token');
  assert(/--overview-gutter:\s*24px/.test(css), 'overview CSS must expose the desktop gutter token');
  assert(/--overview-card-gap:\s*14px/.test(css), 'overview CSS must expose the card gap token');
  assert(/--overview-card-min:\s*340px/.test(css), 'overview CSS must expose the minimum card width token');
  assert(/body\[data-shell-mode=['"]overview['"]\]\s+\.head-row\b[\s\S]*width:\s*min\(100%,\s*calc\(var\(--overview-max-width\) \+ \(2 \* var\(--overview-gutter\)\)\)\)[\s\S]*padding:\s*0\.35rem var\(--overview-gutter\)/.test(css), 'overview header shell width must include gutters so header content edges match overview cards');
  assert(/\.overview-view\b[\s\S]*max-width:\s*var\(--overview-max-width\)/.test(css), 'overview view must consume the semantic max-width token');
  assert(/\.overview-list\b[\s\S]*grid-template-columns:\s*repeat\(3,\s*minmax\(var\(--overview-card-min\),\s*1fr\)\)/.test(css), 'desktop overview must use three columns with the semantic minimum card width');
  assert(/\.overview-list\b[\s\S]*gap:\s*var\(--overview-card-gap\)/.test(css), 'desktop card gutter must use the semantic card gap token');
  assert(/\.overview-card\b[\s\S]*border-radius:\s*(?:24px|1\.5rem)/.test(css), 'server cards must use GPU Monitor card radius');
  assert(/\.overview-card\b[\s\S]*background:\s*var\(--ops-card\)/.test(css), 'server cards must use the shared Clean card surface');
  assert(/--ops-card:\s*var\(--surface\)/.test(css) && /--ops-border:\s*var\(--separator\)/.test(css), 'Storage must expose the same semantic card aliases as GPU Monitor');
  assert(/\.overview-card\b[\s\S]*border:\s*1px solid var\(--ops-border\)/.test(css), 'Storage server cards must consume the shared GPU Monitor border token');
  assert(/\.overview-card\b[\s\S]*background:\s*var\(--ops-card\)/.test(css), 'Storage server cards must consume the shared GPU Monitor card token');
  assert(/html\.light\s+\.overview-card\b[\s\S]*box-shadow:\s*0 8px 22px color-mix\(in srgb, var\(--ops-fg\) 8%, transparent\)/.test(css), 'light-mode Storage overview cards must use a restrained Clean resting shadow');
  assert(/\.overview-card-header\b[\s\S]*padding:\s*0\.56rem 0\.75rem 0\.52rem/.test(css), 'Storage card headers must use GPU Monitor card header rhythm');
  assert(/\.overview-card-header\b[\s\S]*border-bottom:\s*1px solid/.test(css), 'server headers must be separated from mount metrics by one quiet rule');
  assert(/\.overview-mounts\b[\s\S]*display:\s*flex[\s\S]*flex-direction:\s*column/.test(css), 'mounts must render as compact vertical monitor rows');
  assert(/\.overview-mount\b[\s\S]*grid-template-columns:\s*(?:34px|2\.15rem)\s+minmax\(0,\s*1fr\)/.test(css), 'mount rows must align a compact media chip with a flexible metric body');
  assert(/\.overview-mount\[data-pressure="warning"\]\s+\.overview-mount-pct\s*\{[^}]*color:\s*var\(--warn\)/.test(css), 'warning color must be scoped to the exact warning percentage selector');
  assert(/\.overview-mount\[data-pressure="critical"\]\s+\.overview-mount-pct\s*\{[^}]*color:\s*var\(--crit\)/.test(css), 'critical color must be scoped to the exact critical percentage selector');
  assert(/\.overview-pressure-fill\[data-pressure="unknown"\][\s\S]*background:\s*var\(--text2\)/.test(css), 'unknown pressure bars must use a neutral color instead of inheriting OK green');
  assert(/@media\s*\(max-width:\s*1095px\)[\s\S]*\.overview-list\b[\s\S]*grid-template-columns:\s*repeat\(2,\s*minmax\(var\(--overview-card-min\),\s*1fr\)\)/.test(css), 'overview must switch to two columns before three cards would fall below 340px');
  assert(/@media\s*\(max-width:\s*733px\)[\s\S]*\.overview-list\b[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(css), 'overview must switch to one column before two cards would fall below 340px');
  assert(/\.overview-pressure-bar\b[\s\S]*height:\s*4px/.test(css), 'compact overview pressure bars must be 4px tall');
  assert(/\.overview-mount-path\b[\s\S]*text-overflow:\s*ellipsis/.test(css), 'compact mount paths must truncate visually');
  assert(/font-variant-numeric:\s*tabular-nums/.test(css), 'compact numeric fields must use tabular numbers');
  assert(/@media\s*\(max-width:\s*733px\)[\s\S]*--overview-gutter:\s*12px/.test(css), '390px mobile overview layouts must use the approved 12px semantic gutter');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.overview-card\b[\s\S]*overflow:\s*hidden/.test(css), 'mobile overview cards must explicitly hide horizontal overflow');
  assert(/@media\s*\(prefers-reduced-motion:\s*reduce\)/.test(css), 'overview styling must continue to respect reduced motion');
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
    newer.resolve({ server_id: 'alpha-1', hostname: 'alpha-new', mounts: [{ path: '/data', df_total: 1000 * 1024 ** 3, df_used: 910 * 1024 ** 3, df_use_pct: 91, df_avail: 700 * 1024 ** 3 }], users: [], top_files: [], stale: [] });
    await flushPromises();
    assert.strictEqual(viewer.getCurrentDetailDebugState().data.hostname, 'alpha-new', 'newer alpha request should win the detail data');
    assert(overviewRowText(viewer, 'alpha-1').includes('91%'), 'overview should reflect the newer alpha snapshot');

    older.resolve({ server_id: 'alpha-1', hostname: 'alpha-old', mounts: [{ path: '/data', df_total: 1000 * 1024 ** 3, df_used: 110 * 1024 ** 3, df_use_pct: 11, df_avail: 900 * 1024 ** 3 }], users: [], top_files: [], stale: [] });
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
    newer.resolve({ server_id: 'alpha-1', hostname: 'alpha-fresh', mounts: [{ path: '/data', df_total: 1000 * 1024 ** 3, df_used: 770 * 1024 ** 3, df_use_pct: 77, df_avail: 800 * 1024 ** 3 }], users: [], top_files: [], stale: [] });
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


function testThemeModeCookieContractPreservesHistory() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.applyStoredThemeMode, 'function', 'applyStoredThemeMode must be exposed for shell reuse');
  assert.strictEqual(typeof viewer.toggleThemeMode, 'function', 'toggleThemeMode must be exposed for shell reuse');

  viewer.document.cookie = 'themeMode=light; session=ignored';
  const button = viewer.document.getElementById('themeModeButton');
  viewer.applyStoredThemeMode();
  assert.ok(viewer.document.documentElement.classList.contains('light'), 'stored light mode must apply html.light');
  assert.ok(!viewer.document.documentElement.classList.contains('dark'), 'stored light mode must remove html.dark');
  assert.strictEqual(viewer.document.documentElement.dataset.material, 'liquid', 'Storage shell must use Clean liquid material');
  assert.strictEqual(button.getAttribute('aria-pressed'), 'false', 'aria-pressed reflects whether dark mode is active');

  viewer.__historyCalls.length = 0;
  viewer.toggleThemeMode();
  assert.ok(viewer.document.documentElement.classList.contains('dark'), 'toggle from light must apply dark');
  assert.ok(!viewer.document.documentElement.classList.contains('light'), 'toggle from light must remove light');
  assert.strictEqual(button.getAttribute('aria-pressed'), 'true', 'theme button pressed state updates after toggle');
  assert.match(viewer.document.cookie, /themeMode=dark; Path=\/; SameSite=Lax/, 'toggle must persist the GPU Monitor themeMode cookie contract');
  assert.deepStrictEqual(viewer.__historyCalls, [], 'theme toggles must not touch route/history state');
}

function testThemeControlAndTileFirstDetailLayoutContract() {
  const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');
  const app = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
  const button = html.slice(html.indexOf('id="themeModeButton"'), html.indexOf('</button>', html.indexOf('id="themeModeButton"')));
  assert.match(button, /theme-icon-sun/);
  assert.match(button, /theme-icon-moon/);
  assert.match(css, /\.theme-mode-button\s*\{[^}]*width:\s*40px[^}]*height:\s*40px[^}]*border-radius:\s*50%/);
  assert.match(css, /\.theme-mode-button\s+\.theme-icon\s*\{[^}]*position:\s*absolute[^}]*transition:[^}]*opacity[^}]*transform/);
  assert.doesNotMatch(css, /html\.dark\s+\.theme-icon-moon\s*,\s*html\.light\s+\.theme-icon-sun\s*\{[^}]*display:\s*none/);
  assert.match(app, /document\.body\.dataset\.shellMode\s*=\s*isDetail\s*\?\s*"detail"\s*:\s*"overview"/);
  assert.match(css, /\.caps\.detail-capacity-rail\s*\{[^}]*grid-template-columns:\s*repeat\(6,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(css, /body\[data-shell-mode=['"]detail['"]\]\s+main\s*\{[^}]*padding-top:\s*8px/);
  assert.match(css, /#treemap\s*\{[^}]*height:\s*calc\(100vh\s*-\s*225px\)/);
}

function testTreemapLegendOverlaysWithoutReducingTileViewport() {
  const css = fs.readFileSync(path.join(__dirname, 'styles.css'), 'utf8');
  const treemap = fs.readFileSync(path.join(__dirname, 'treemap.js'), 'utf8');
  assert.match(css, /#panel-treemap\.active\s*\{[^}]*position:\s*relative/);
  assert.match(css, /#panel-treemap\s+\.legend\s*\{[^}]*position:\s*absolute[^}]*flex-wrap:\s*nowrap[^}]*overflow-x:\s*auto/);
  assert.match(css, /\.tm-scale-note\s*\{[^}]*bottom:\s*52px/, 'scale note must clear the overlaid legend rail');
  assert.doesNotMatch(treemap, /const\s+legendH\s*=\s*legend\s*\?\s*legend\.offsetHeight/);
  assert.match(treemap, /const\s+avail\s*=\s*main\.clientHeight\s*-\s*pad\s*-\s*toolbarH\s*-\s*12/);
}

async function main() {
  testThemeModeCookieContractPreservesHistory();
  testThemeControlAndTileFirstDetailLayoutContract();
  testTreemapLegendOverlaysWithoutReducingTileViewport();
  testOverviewRenderingKeepsStableOrderAndVisibleCapacityBars();
  testSnapshotLoadFailureRendersAsVisibleException();
  testRouteNavigationAndBackShellContract();
  await testBootstrapDetectionIsExplicitAndSequential();
  await testApiBootstrapRendersBeforeSlowSnapshotsAndStreamsCompletedServers();
  await testApiBootstrapUsesEmbeddedOverviewSnapshotsWithoutFullDownloads();
  await testOverviewOnlySnapshotNeverSatisfiesDetailLoading();
  testOverviewHydrationCannotOverwriteANewerDetailRequest();
  testSampleMarkerAndCompactOverviewOmitsAggregateSurface();
  testMountCentricOverviewDomFieldsAndStableNavigation();
  testMountStatusTextAppearsOnlyForExceptionalPressure();
  testServerHeaderMetaIsActionableMountCountOnly();
  testUnknownMountCapacityDomStaysNeutralAndAccessible();
  await testDetailCapacityUsesCompactRowsAndFiltersBootMounts();
  await testDetailNormalizationFiltersBootEverywhereAndRecomputesUsers();
  await testDetailCapacityUnknownNumbersRenderNeutralDashes();
  await testZeroActionableMountsUseExactKoreanEmptyCopy();
  testDetailCapacityResponsiveCssContract();
  testDetailHeaderMobileCssContract();
  testOverviewMonitorCardHierarchyContract();
  testOverviewMasonryPreservesOrderAcrossResponsiveColumns();
  testOverviewLayoutClearsStaleInlinePlacementAndMotion();
  testOverviewFlipRunsOnlyOnColumnCountChangeAndSkipsReducedMotion();
  testOverviewFlipCapturesAllFirstRectsBeforeClearingPlacement();
  testOverviewFlipIgnoresStaleCallbacksAfterRapidRelayout();
  testOverviewResizeFallbackSchedulesOnlyOverviewRelayout();
  testApprovedCleanThemeTokenContract();
  testSuccessfulOverviewSuppressesServerCountLiveLead();
  testMountCentricResponsiveCssContract();
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
