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
    this.offsetHeight = 0;
    this.clientHeight = 640;
    this.clientWidth = 1000;
  }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this); }
  set innerHTML(value) { this._innerHTML = String(value); this.children = []; }
  get innerHTML() { return this._innerHTML || ''; }
  set textContent(value) { this._textContent = String(value); }
  get textContent() { return this._textContent || ''; }
  setAttribute(name, value) { this[name] = String(value); }
  getAttribute(name) { return this[name]; }
  addEventListener() {}
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
    'advisor-client.js',
    'advisor-ui.js',
    'advisor-badges.js',
    'treemap.js',
    'tables.js',
    'app.js',
  ], 'viewer code must be loaded from ordered external scripts');
  assert(!/<style\b/i.test(html), 'viewer stylesheet must be externalized');
  assert(html.includes('<link rel="stylesheet" href="styles.css">'), 'index must link styles.css');
  assert(html.includes('data-tab="advisor"'), 'index must expose an AI Advisor tab');
  assert(html.includes('id="panel-advisor"'), 'index must contain the AI Advisor panel');
  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) elements.set(id, new FakeElement('div'));
    return elements.get(id);
  };
  const context = {
    console,
    Math,
    Date,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: (fn) => fn(),
    window: {
      innerWidth: 1200,
      innerHeight: 800,
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
  return context;
}

function numericPx(value) { return Number(String(value || '0').replace(/px$/, '')); }

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
