const HOUR_MS = 3600e3;
const UNITS = [[1e9,'Gb/s'],[1e6,'Mb/s'],[1e3,'Kb/s'],[1,'b/s']];
const FALLBACK = {accent:'#3a8cff',ok:'#00b793',text:'#8f9aa4',line:'#26292e',surface:'#13161b',fg:'#f0f2f4'};
const refsOf = new WeakMap();

export function formatRate(bps) {
  if (bps == null || !Number.isFinite(bps)) return {value:'—',unit:'',text:'—'};
  let index = UNITS.findIndex(([size]) => bps >= size);
  if (index < 0) index = UNITS.length - 1;
  const render = i => {
    const v = bps / UNITS[i][0];
    return i === UNITS.length - 1 ? String(Math.round(v)) : v.toFixed(v < 100 ? 1 : 0);
  };
  let value = render(index);
  if (index > 0 && Number(value) >= 1000) value = render(--index);
  return {value,unit:UNITS[index][1],text:value + ' ' + UNITS[index][1]};
}

const pad = n => String(n).padStart(2,'0');
export function clockLabel(unix, nowUnix) {
  const at = new Date(unix * 1000), now = new Date(nowUnix * 1000);
  const days = Math.round((new Date(now.getFullYear(),now.getMonth(),now.getDate()) - new Date(at.getFullYear(),at.getMonth(),at.getDate())) / 864e5);
  return (days === 1 ? '어제 ' : days > 1 ? (at.getMonth() + 1) + '/' + at.getDate() + ' ' : '') + pad(at.getHours()) + ':' + pad(at.getMinutes());
}

const direction = arrow => arrow + (arrow === '↓' ? ' 받기' : ' 보내기');
const serverName = id => id.charAt(0).toUpperCase() + id.slice(1);

// What the card shows for one link; `failed` is true when the latest poll could not reach the dashboard.
export function networkModel(link, nowUnix, failed = false) {
  const name = serverName(link.server_id), state = failed && link.state === 'ok' ? 'stale' : link.state;
  const live = state === 'ok' && link.now;
  const capacity = formatRate(link.capacity_bps);
  const peak = link.peak, rxHigher = peak && peak.rx_bps >= peak.tx_bps;
  const top = peak && (rxHigher ? {arrow:'↓',bps:peak.rx_bps,unix:peak.rx_unix} : {arrow:'↑',bps:peak.tx_bps,unix:peak.tx_unix});
  const other = peak && (rxHigher ? {arrow:'↑',bps:peak.tx_bps,unix:peak.tx_unix} : {arrow:'↓',bps:peak.rx_bps,unix:peak.rx_unix});
  return {
    name, state,
    title: name + ' 네트워크',
    subtitle: '스토리지 스위치 · ' + capacity.text + ' 링크',
    note: state === 'ok' ? '' : failed ? '대시보드에 연결하지 못해 마지막 값을 표시합니다.' :
      state === 'error' ? '스위치에서 값을 읽지 못하고 있습니다. 마지막 기록을 표시합니다.' : '최근 30초 동안 새 측정값이 없습니다.',
    rx: {label:'↓ 받기 (' + name + '로)', rate:formatRate(live ? link.now.rx_bps : null)},
    tx: {label:'↑ 보내기 (' + name + '에서)', rate:formatRate(live ? link.now.tx_bps : null)},
    peak: peak ? {rate:formatRate(top.bps), caption:direction(top.arrow) + ' · ' + clockLabel(top.unix,nowUnix),
      meta:direction(other.arrow) + ' 최고 ' + formatRate(other.bps).text + ' · ' + clockLabel(other.unix,nowUnix)} :
      {rate:formatRate(null), caption:'아직 기록 없음', meta:''},
  };
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
}
const rateBlock = (cls) => {
  const root = node('div','network-stat ' + cls), label = node('span','network-label');
  const dot = node('i','network-dot'), labelText = node('span'), rate = node('strong','network-rate');
  const value = node('span'), unit = node('small'), meta = node('span','network-meta');
  label.appendChild(dot); label.appendChild(labelText);
  rate.appendChild(value); rate.appendChild(unit);
  [label,rate,meta].forEach(child => root.appendChild(child));
  return {root,labelText,value,unit,meta};
};
function buildCard(serverId) {
  const root = node('section','network-card'), head = node('header','network-head'), titles = node('div');
  const title = node('h3'), subtitle = node('p','network-sub'), state = node('span','network-state');
  titles.appendChild(title); titles.appendChild(subtitle);
  head.appendChild(titles); head.appendChild(state);
  const stats = node('div','network-stats'), rx = rateBlock('network-rx'), tx = rateBlock('network-tx'), peak = rateBlock('network-peak');
  [rx,tx,peak].forEach(block => stats.appendChild(block.root));
  const note = node('p','network-note'), unit = node('span','network-chart-unit'), chart = node('div','network-chart');
  chart.setAttribute('role','img');
  [head,stats,note,unit,chart].forEach(child => root.appendChild(child));
  root.dataset.server = serverId;
  const refs = {title,subtitle,state,rx,tx,peak,note,unit,chart};
  refsOf.set(root,refs);
  return root;
}
function paint(block, label, rate, meta) {
  block.labelText.textContent = label;
  block.value.textContent = rate.value;
  block.unit.textContent = rate.unit;
  block.meta.textContent = meta;
}

function themeColors() {
  if (typeof getComputedStyle !== 'function' || typeof document === 'undefined' || !document.documentElement?.nodeType) return FALLBACK;
  const style = getComputedStyle(document.documentElement), read = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  return {accent:read('--accent',FALLBACK.accent),ok:read('--ok',FALLBACK.ok),text:read('--text2',FALLBACK.text),
    line:read('--separator',FALLBACK.line),surface:read('--surface',FALLBACK.surface),fg:read('--text',FALLBACK.fg)};
}

// One point per minute, with a null where a minute has no samples so the line breaks there.
function series(buckets, valueIndex, divisor) {
  const points = [];
  buckets.forEach((row,i) => {
    if (i && row[0] - buckets[i - 1][0] > 60) points.push([(buckets[i - 1][0] + 60) * 1000,null]);
    points.push([row[0] * 1000,row[valueIndex] / divisor]);
  });
  return points;
}
// The axis is Gb/s once any minute reaches 1 Gb/s and Mb/s below that.
export function chartScale(link) {
  return link.buckets.some(row => row[2] >= 1e9 || row[4] >= 1e9) ? UNITS[0] : UNITS[1];
}
export function chartOption(link, colors, nowUnix) {
  const buckets = link.buckets, [axisDivisor] = chartScale(link);
  const byMinute = new Map(buckets.map(row => [row[0] * 1000,row]));
  const name = serverName(link.server_id);
  // The per-minute max is what the line shows, so short bursts stay visible; the tooltip adds the average.
  const line = (key, color, valueIndex) => ({
    name:key, type:'line', showSymbol:false, animation:false, connectNulls:false,
    lineStyle:{color,width:1.6},
    areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:color + '55'},{offset:1,color:color + '05'}]}},
    data:series(buckets,valueIndex,axisDivisor),
  });
  const mono = 'ui-monospace, SFMono-Regular, Menlo, monospace';
  return {
    animation:false,
    grid:{left:4,right:12,top:10,bottom:2,containLabel:true},
    tooltip:{
      trigger:'axis', confine:true, backgroundColor:colors.surface, borderColor:colors.line, borderWidth:1,
      textStyle:{color:colors.fg,fontFamily:mono,fontSize:12}, extraCssText:'border-radius:10px',
      axisPointer:{type:'line',lineStyle:{color:colors.text,type:'dashed',opacity:0.5}},
      formatter(params) {
        const ms = params[0]?.value?.[0], row = byMinute.get(ms);
        if (!row) return '';
        const dot = color => '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + color + ';margin-right:6px"></span>';
        const entry = (color, label, avg, max) => dot(color) + label + '  <b>' + formatRate(max).text + '</b> <span style="opacity:.6">최고 · 평균 ' + formatRate(avg).text + '</span>';
        return clockLabel(ms / 1000,nowUnix) + '<br>' + entry(colors.accent,'받기 (' + name + '로)',row[1],row[2]) +
          '<br>' + entry(colors.ok,'보내기 (' + name + '에서)',row[3],row[4]);
      },
    },
    xAxis:{
      type:'time', min:(nowUnix - 24 * 3600) * 1000, max:nowUnix * 1000, splitNumber:6, minInterval:HOUR_MS,
      axisLine:{lineStyle:{color:colors.line}}, axisTick:{show:false}, splitLine:{show:false},
      axisLabel:{color:colors.text,fontFamily:mono,fontSize:11,hideOverlap:true,
        formatter:value => {
          const at = new Date(value);
          return at.getHours() === 0 && at.getMinutes() === 0 ? (at.getMonth() + 1) + '/' + at.getDate() : pad(at.getHours()) + ':' + pad(at.getMinutes());
        }},
    },
    yAxis:{
      type:'value', min:0, splitNumber:4, axisLine:{show:false}, axisTick:{show:false},
      splitLine:{lineStyle:{color:colors.line,type:'dashed',opacity:0.7}},
      axisLabel:{color:colors.text,fontFamily:mono,fontSize:11,formatter:value => String(+value.toFixed(1))},
    },
    series:[line('rx',colors.accent,2),line('tx',colors.ok,4)],
  };
}

// Times come from the payload's generated_unix, so a browser with a wrong clock still draws the right 24 h.
export function renderNetwork(container, resource, {echarts, resize = false} = {}) {
  const links = resource?.data?.links ?? [], nowUnix = resource?.data?.generated_unix;
  container.hidden = !links.length;
  const wanted = new Set(links.map(link => link.server_id));
  for (const card of [...container.children]) if (!wanted.has(card.dataset.server)) card.remove();
  for (const link of links) {
    let card = [...container.children].find(child => child.dataset.server === link.server_id);
    if (!card) container.appendChild(card = buildCard(link.server_id));
    const refs = refsOf.get(card), model = networkModel(link,nowUnix,resource.status === 'error');
    card.dataset.state = model.state;
    refs.title.textContent = model.title;
    refs.subtitle.textContent = model.subtitle;
    refs.state.textContent = model.state === 'ok' ? '실시간' : model.state === 'error' ? '연결 끊김' : '지연';
    refs.note.textContent = model.note;
    refs.note.hidden = !model.note;
    paint(refs.rx,model.rx.label,model.rx.rate,'');
    paint(refs.tx,model.tx.label,model.tx.rate,'');
    paint(refs.peak,'24시간 최고',model.peak.rate,model.peak.caption + (model.peak.meta ? '\n' + model.peak.meta : ''));
    refs.chart.setAttribute('aria-label',model.title + ' 24시간 처리량 그래프');
    refs.chart.hidden = !link.buckets.length;
    refs.unit.hidden = !link.buckets.length;
    if (!echarts || !link.buckets.length) continue;
    refs.unit.textContent = chartScale(link)[1];
    const chart = echarts.getInstanceByDom(refs.chart) || echarts.init(refs.chart,null,{renderer:'canvas'});
    if (resize) chart.resize();
    chart.setOption(chartOption(link,themeColors(),nowUnix),true);
  }
}
