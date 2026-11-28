import test from 'node:test';
import assert from 'node:assert/strict';
import {documentFixture} from './dom.js';
import {formatRate,clockLabel,networkModel,chartOption,chartScale,renderNetwork} from '../views/network.js';

const at = (h,m,day = 28) => new Date(2026,10,day,h,m).getTime() / 1000;
const NOW = at(15,0);
const link = (over = {}) => ({server_id:'nexus',interface:'bond_nas',capacity_bps:200e9,state:'ok',
  now:{rx_bps:22.4e9,tx_bps:0.23e9,unix:NOW},
  peak:{rx_bps:22.4e9,rx_unix:at(14,32),tx_bps:8.1e9,tx_unix:at(9,10,27)},
  buckets:[[at(14,30),5e9,6e9,1e8,2e8],[at(14,31),20e9,22.4e9,1e8,2e8],[at(14,34),1e9,2e9,1e8,8.1e9]],...over});
const colors = {accent:'#3a8cff',ok:'#00b793',text:'#888',line:'#333',surface:'#111',fg:'#fff'};

test('rates read in Gb/s, Mb/s, Kb/s and b/s without rounding into 1000', () => {
  assert.equal(formatRate(22.43e9).text,'22.4 Gb/s');
  assert.equal(formatRate(150e9).text,'150 Gb/s');
  assert.equal(formatRate(230e6).text,'230 Mb/s');
  assert.equal(formatRate(999.96e6).text,'1.0 Gb/s');
  assert.equal(formatRate(1500).text,'1.5 Kb/s');
  assert.equal(formatRate(0).text,'0 b/s');
  assert.equal(formatRate(null).text,'—');
});

test('peak time is a clock time, with a day marker once it is not today', () => {
  assert.equal(clockLabel(at(14,32),NOW),'14:32');
  assert.equal(clockLabel(at(9,5,27),NOW),'어제 09:05');
});

test('the card model names the direction from the server and leads with the larger peak', () => {
  const model = networkModel(link(),NOW);
  assert.equal(model.title,'Nexus 네트워크');
  assert.equal(model.rx.label,'↓ 받기 (Nexus로)');
  assert.equal(model.tx.label,'↑ 보내기 (Nexus에서)');
  assert.equal(model.rx.rate.text,'22.4 Gb/s');
  assert.equal(model.peak.rate.text,'22.4 Gb/s');
  assert.equal(model.peak.caption,'↓ 받기 · 14:32');
  assert.equal(model.peak.meta,'↑ 보내기 최고 8.1 Gb/s · 어제 09:10');
  assert.equal(model.subtitle,'스토리지 스위치 · 200 Gb/s 링크');
});

test('a stale link shows a quiet note and no live rate but keeps the peak', () => {
  const model = networkModel(link({state:'stale'}),NOW);
  assert.equal(model.rx.rate.text,'—');
  assert.match(model.note,/30초/);
  assert.equal(model.peak.rate.text,'22.4 Gb/s');
  assert.match(networkModel(link({state:'error'}),NOW).note,/스위치/);
  assert.match(networkModel(link(),NOW,true).note,/대시보드/);
  assert.equal(networkModel(link(),NOW).note,'');
});

test('the chart draws per-minute max, breaks the line at a gap and picks the unit from the data', () => {
  const option = chartOption(link(),colors,NOW);
  assert.equal(option.series.length,2);
  const rx = option.series[0].data;
  assert.deepEqual(rx.map(p => p[1]),[6,22.4,null,2]);
  assert.equal(rx[2][0],(at(14,31) + 60) * 1000);
  assert.deepEqual(chartScale(link()),[1e9,'Gb/s']);
  assert.deepEqual(chartScale(link({buckets:[[at(14,30),5e6,6e6,1e6,2e6]]})),[1e6,'Mb/s']);
  assert.equal(option.xAxis.max,NOW * 1000);
  const tip = option.tooltip.formatter([{value:[at(14,31) * 1000,20]}]);
  assert.match(tip,/22\.4 Gb\/s/);
  assert.match(tip,/평균 20\.0 Gb\/s/);
  assert.match(tip,/보내기 \(Nexus에서\)/);
});

function setup() {
  globalThis.document = documentFixture();
  const container = document.createElement('div');
  const drawn = [];
  const echarts = {getInstanceByDom:() => null,init:() => ({resize() {},setOption:(option,replace) => drawn.push({option,replace})})};
  return {container,drawn,echarts};
}
const texts = node => [node.textContent,...node.children.flatMap(texts)];

test('the card renders the peak and live rates from a payload and draws the chart', () => {
  const {container,drawn,echarts} = setup();
  renderNetwork(container,{status:'ready',data:{links:[link()],generated_unix:NOW}},{echarts});
  assert.equal(container.hidden,false);
  assert.equal(container.children.length,1);
  const text = texts(container.children[0]).join('|');
  for (const expected of ['Nexus 네트워크','24시간 최고','22.4','Gb/s','↓ 받기 · 14:32\n↑ 보내기 최고 8.1 Gb/s · 어제 09:10']) assert.ok(text.includes(expected),expected);
  assert.equal(container.children[0].dataset.state,'ok');
  assert.equal(drawn.length,1);
  assert.equal(drawn[0].replace,true);
  renderNetwork(container,{status:'ready',data:{links:[link()],generated_unix:NOW + 5}},{echarts});
  assert.equal(container.children.length,1,'a refresh updates the card in place');
});

test('the card is absent with no links, before the first load and after a failed first load', () => {
  const {container,echarts} = setup();
  renderNetwork(container,{status:'ready',data:{links:[]}},{echarts});
  assert.equal(container.hidden,true);
  assert.equal(container.children.length,0);
  renderNetwork(container,undefined,{echarts});
  assert.equal(container.hidden,true);
  renderNetwork(container,{status:'error',code:'NOT_FOUND'},{echarts});
  assert.equal(container.hidden,true);
  renderNetwork(container,{status:'ready',data:{links:[link()],generated_unix:NOW}},{echarts});
  assert.equal(container.hidden,false);
  renderNetwork(container,{status:'ready',data:{links:[]}},{echarts});
  assert.equal(container.children.length,0);
});

test('a failed refresh keeps the last data with a note, and the headline renders before echarts loads', () => {
  const {container,drawn} = setup();
  renderNetwork(container,{status:'error',code:'X',data:{links:[link()],generated_unix:NOW}},{echarts:undefined});
  const card = container.children[0];
  assert.equal(card.dataset.state,'stale');
  assert.match(texts(card).join('|'),/대시보드/);
  assert.equal(drawn.length,0);
});
