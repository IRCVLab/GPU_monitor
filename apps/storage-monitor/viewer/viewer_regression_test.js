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
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.className = '';
    this.classList = new FakeClassList(this);
    this.attributes = {};
    this.offsetHeight = 0;
    this.clientHeight = 640;
    this.clientWidth = 1000;
    this.hidden = false;
    this.value = '';
    this.listeners = new Map();
  }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
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
  assert(html.includes('id="overviewView"'), 'overview shell must be present');
  assert(html.includes('id="overviewList"'), 'overview list container must be present');
  assert(html.includes('id="overviewBack"'), 'detail back-to-overview control must be present');
  const removedName = 'ad' + 'visor';
  assert(!html.includes('data-tab="' + removedName + '"'), 'removed analysis tab must not exist');
  assert(!html.includes('id="panel-' + removedName + '"'), 'removed analysis panel must not exist');

  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) {
      const el = new FakeElement('div');
      el.id = id;
      elements.set(id, el);
    }
    return elements.get(id);
  };
  const historyCalls = [];
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
    document: {
      createElement: (tag) => new FakeElement(tag),
      getElementById: getEl,
      querySelector: () => new FakeElement('div'),
      querySelectorAll: () => [],
      addEventListener() {},
      body: new FakeElement('body'),
    },
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
  return context;
}

function textTree(node) {
  let out = node.textContent || '';
  if (node.innerHTML) out += node.innerHTML;
  for (const child of node.children || []) out += textTree(child);
  return out;
}

function numericPx(value) { return Number(String(value || '0').replace(/px$/, '')); }

(function testOverviewRenderingKeepsStableOrderAndVisibleCapacityBars() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.buildOverviewServer, 'function', 'overview row builder must be exposed');
  assert.strictEqual(typeof viewer.renderOverviewList, 'function', 'overview renderer must be exposed');

  const rows = [
    viewer.buildOverviewServer(
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
    ),
    viewer.buildOverviewServer(
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
    ),
  ];

  const overviewList = viewer.__elements.get('overviewList') || viewer.document.getElementById('overviewList');
  const opened = [];
  viewer.renderOverviewList(overviewList, rows, { onOpenServer: (serverId) => opened.push(serverId) });

  assert.strictEqual(overviewList.children.length, 2, 'all servers must render into one dense list');
  assert.strictEqual(overviewList.children[0].dataset.serverId, 'beta-2', 'inventory order must not change because of severity');
  assert.strictEqual(overviewList.children[1].dataset.serverId, 'alpha-1', 'inventory order must remain stable for later rows');

  const betaText = textTree(overviewList.children[0]);
  assert(betaText.includes('beta'), 'row must contain the server display name');
  assert(betaText.includes('스캔 실패'), 'higher-priority exceptional status must render concise Korean text');
  assert(betaText.includes('스캔 중'), 'active scan must remain visible as a secondary cue');
  assert(betaText.includes('95%'), 'capacity bars must still show percentage text');
  assert(betaText.includes('800 GB'), 'capacity bars must still show available-byte text');

  const alphaText = textTree(overviewList.children[1]);
  assert(alphaText.includes('오래됨'), 'stale freshness must render as an exceptional state');
  assert(alphaText.includes('81%'), 'warning-capacity rows must still expose their compact bar text');

  overviewList.children[0].onclick();
  assert.deepStrictEqual(opened, ['beta-2'], 'clicking a row should open its detail workspace');
  let prevented = false;
  overviewList.children[1].onkeydown({ key: 'Enter', preventDefault() { prevented = true; } });
  assert.strictEqual(prevented, true, 'Enter activation must prevent duplicate default behavior');
  assert.deepStrictEqual(opened, ['beta-2', 'alpha-1'], 'Enter should activate the row');
})();

(function testRouteNavigationAndBackShellContract() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.applyRouteState, 'function', 'route application helper must be exposed');
  assert.strictEqual(typeof viewer.navigateToOverview, 'function', 'overview navigation helper must be exposed');

  const overviewView = viewer.document.getElementById('overviewView');
  const detailView = viewer.document.getElementById('detailView');
  const back = viewer.document.getElementById('overviewBack');
  viewer.applyRouteState({ serverId: null, tab: 'treemap' }, { skipHistory: true });
  assert.strictEqual(overviewView.hidden, false, 'overview route must show the overview shell');
  assert.strictEqual(detailView.hidden, true, 'overview route must hide the detail shell');
  viewer.applyRouteState({ serverId: 'beta-2', tab: 'users' }, { skipHistory: true });
  assert.strictEqual(overviewView.hidden, true, 'detail route must hide the overview shell');
  assert.strictEqual(detailView.hidden, false, 'detail route must show the detail shell');
  assert.strictEqual(back.hidden, false, 'detail route must expose the back-to-overview control');
  viewer.navigateToOverview({ skipHistory: true });
  assert.strictEqual(overviewView.hidden, false, 'back navigation must restore the overview shell');
  assert.strictEqual(detailView.hidden, true, 'back navigation must hide the detail shell again');
})();

(function testDeleteCommandQuoting() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.shellQuotePath, 'function', 'shellQuotePath must be exposed for command generation');
  assert.strictEqual(typeof viewer.buildDeleteCommands, 'function', 'buildDeleteCommands must be exposed for command generation');
  assert.strictEqual(typeof viewer.cleanupCheckboxHtml, 'function', 'cleanupCheckboxHtml must be exposed for table rows');
  assert.strictEqual(typeof viewer.renderCleanupPanel, 'function', 'renderCleanupPanel must be exposed for selection updates');
  assert.strictEqual(typeof viewer.bindCleanupSelection, 'function', 'bindCleanupSelection must be exposed before app init');
  assert.strictEqual(viewer.shellQuotePath('/data/simple file.bin'), "'/data/simple file.bin'");
  assert.strictEqual(viewer.shellQuotePath("/data/O'Hara/checkpoint.pt"), "'/data/O'\"'\"'Hara/checkpoint.pt'");

  const commands = viewer.buildDeleteCommands([
    { path: '/data/simple file.bin' },
    { path: "/data/O'Hara/checkpoint.pt" },
  ]);
  assert.deepStrictEqual(Array.from(commands.split('\n')), [
    "sudo rm -rf -- '/data/simple file.bin'",
    "sudo rm -rf -- '/data/O'\"'\"'Hara/checkpoint.pt'",
  ]);
})();

(function testTreemapHidesMicroTilesInsteadOfInflatingThem() {
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
})();

(function testTreemapTilesUseSelectionModeInsteadOfPerTileCheckboxes() {
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
  assert.strictEqual(idleCopy.label, 'Ctrl/⌘ click: select');
  assert.strictEqual(idleCopy.hint, 'Click: drill · Ctrl/⌘ click: select cleanup candidate · 클릭: 열기 · Ctrl/⌘ 클릭: 정리 후보 선택');
  const activeCopy = viewer.treemapShortcutCopy(true);
  assert.strictEqual(activeCopy.label, 'Selecting… / 선택 중');
  assert.strictEqual(activeCopy.hint, 'Release Ctrl/⌘ to leave selection mode · 키를 떼면 선택 모드 종료');

  const root = {
    name: '/data',
    bytes: 600,
    other_bytes: 100,
    uid: 1001,
    children: [
      { name: 'project', bytes: 500, uid: 1001, files: 20, children: [] },
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
  assert.ok(project.children.some(c => c.className === 'tm-cleanup-badge'), 'real-path tile should have a hidden selected-state badge');
  assert.ok(!other.dataset.cleanupPath, 'aggregate non-path tiles must not be selectable');
  assert.ok(!other.children.some(c => c.className === 'tm-cleanup-badge'), 'aggregate non-path tiles must not show cleanup controls');
})();

(function testTreemapGroupTilesStayBehindDescendants() {
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
})();

(function testTreemapDoesNotExposeTopLevelCleanupCandidates() {
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
})();

(function testTreemapScaleNoteExplainsHiddenTinyItemsBilingually() {
  const viewer = loadViewer();
  assert.strictEqual(typeof viewer.renderTreemapScaleNote, 'function', 'scale note renderer should be testable');
  const el = new FakeElement('div');
  el._tmHiddenItems = { bytes: 1280, count: 39 };
  viewer.renderTreemapScaleNote(el);
  const note = el.children.find(c => c.className === 'tm-scale-note');
  assert.ok(note, 'scale note should be rendered when tiny items are hidden');
  assert.ok(note.innerHTML.includes('too small to draw proportionally'), 'English note should explain why items are hidden');
  assert.ok(note.innerHTML.includes('실제 비율'), 'Korean note should explain true-scale hiding');
})();

console.log('viewer regression tests passed');
