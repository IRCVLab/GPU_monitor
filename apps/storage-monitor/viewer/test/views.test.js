import test from 'node:test';
import assert from 'node:assert/strict';
import {renderHeader} from '../views/header.js';
import {drawUsers} from '../views/users.js';
import {normalizeDetail} from '../api.js';
import {documentFixture} from './dom.js';
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
      bytes: 200 - i, by_volume: { "/home": {bytes:200 - i - (i === 0 ? 50 : 0)}, "/data": {bytes:i === 0 ? 50 : 0} },
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
test('V2 header renders a multi-root volume once with the API severity and percent', () => {
  globalThis.document = documentFixture();
  const volume = {volume_id:'v',mountpoint:'/data',media:'ssd',severity:'critical',
    capture:{outcome:'complete'},capacity:{total:100,used:32,avail:68,used_pct:32},
    roots:[{binding:'a',scan_root:'/data/a'},{binding:'b',scan_root:'/data/b'}]};
  const detail = normalizeDetail({host:{hostname:'alpha'},volumes:[volume],users:[],top_files:[],stale:[]});
  renderHeader(detail,null,()=>{});
  const caps = document.getElementById('caps');
  assert.equal(caps.children.length,1);
  assert.equal(caps.children[0].getAttribute('data-pressure'),'critical');
  assert.match(caps.children[0].innerHTML,/32<span>%/);
  assert.equal(caps.children[0].children.at(-1).children.length,2);
});
