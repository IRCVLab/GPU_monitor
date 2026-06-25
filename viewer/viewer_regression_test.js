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
    'treemap.js',
    'tables.js',
    'app.js',
  ], 'viewer code must be loaded from ordered external scripts');
  assert(!/<style\b/i.test(html), 'viewer stylesheet must be externalized');
  assert(html.includes('<link rel="stylesheet" href="styles.css">'), 'index must link styles.css');
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

console.log('viewer regression tests passed');
