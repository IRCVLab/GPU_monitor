import {escapeHtml, fmtAge, fmtDate, humanBytes} from '../format.js';
import {sortRows, columnValue, updateArrows} from '../columns.js';
import {validateCleanupSelection} from '../removal.js';
function pathCellHtml(path) {
  const idx = path.lastIndexOf("/");
  const dir = idx >= 0 ? path.slice(0, idx + 1) : "", file = idx >= 0 ? path.slice(idx + 1) : path;
  const escapedPath = escapeHtml(path).replace(/\r/g, "&#13;");
  const copy = path.includes("\uFFFD")
    ? '<button class="copybtn" disabled title="Path may contain undecodable bytes">Copy</button>'
    : '<button class="copybtn" data-copy="' + escapedPath + '" title="Copy full path">Copy</button>';
  return '<div class="pathwrap"><span class="pathtext" title="' + escapedPath + '"><bdi>' +
    '<span class="dir">' + escapeHtml(dir) + '</span><span class="file">' + escapeHtml(file) + '</span></bdi></span>' +
    copy + '</div>';
}
function sizeCellHtml(bytes, maxBytes) {
  const w = maxBytes > 0 ? Math.max(2, bytes / maxBytes * 100) : 0;
  return '<span class="size-bar" style="width:' + w.toFixed(1) + '%"></span><span class="size-val">' + humanBytes(bytes) + '</span>';
}
function ageColor(age, maxAge) {
  const t = Math.min(1, (age || 0) / (maxAge || 1));
  return "rgba(255,59,48," + (0.04 + t * 0.16).toFixed(3) + ")";
}

export function renderFiles({DATA, route, colorForUid, entries, volumeLabels, mountPaths, sort, navigate}) {
  const stale = route.tab === 'stale';
  const prefix = stale ? 'stale' : 'top';
  const table = stale ? '#staleHead' : '#topTbl';
  const tbody = stale ? document.getElementById('staleRows') : document.querySelector('#topTbl tbody');
  const list = stale ? DATA.stale : DATA.top_files;
  const ownerFilter = document.getElementById(stale ? 'staleOwnerFilter' : 'ownerFilter');
  const owners = new Map(list.map(row => [row.uid, row.owner || ('uid ' + row.uid)]));
  ownerFilter.replaceChildren(new Option('All',''), ...[...owners].sort((a,b) => a[1].localeCompare(b[1]))
    .map(([uid,name]) => new Option(name, String(uid))));
  ownerFilter.value = route.owner ?? '';
  const mounts = document.getElementById(prefix + 'MountSeg');
  mounts.replaceChildren(...['',...mountPaths].map(path => {
    const button = document.createElement('button');
    button.classList.toggle('active',path === (route.mount || ''));
    button.textContent = volumeLabels[path] ?? 'All';
    button.onclick = () => navigate({mount:path || null});
    return button;
  }));
  const scoped = list.map(file => ({...file,mount:file.volume_id}))
    .filter(file => (route.owner == null || route.owner === file.uid) && (!route.mount || route.mount === file.mount));
  const rows = sortRows(route.tab,scoped,sort,route);
  const bytes = rows.reduce((sum,row) => sum + row.bytes,0);
  const maxBytes = rows.reduce((max,row) => Math.max(max,row.bytes),0);
  const maxAge = rows.reduce((max,row) => Math.max(max,row.age_days || 0),1);
  document.getElementById(prefix + 'Count').textContent = rows.length.toLocaleString() + ' files · ' + humanBytes(bytes) + (stale ? ' reclaimable' : ' total');
  if (stale) document.getElementById('staleCaption').textContent =
    'Big, long-untouched files — inspect these before cleanup. Reviewing or removing everything listed would reclaim ' + humanBytes(bytes) + '. Verify ownership before deleting.';
  updateArrows(table,sort);
  function rowHtml(row) {
    const valid = validateCleanupSelection(DATA,row);
    const pressed = entries.some(entry => entry.path === row.path);
    const button = valid.accepted ? '<button type="button" class="cleanup-select-btn' + (pressed ? ' is-selected' : '') +
      '" data-binding="' + escapeHtml(row.binding) + '" data-cleanup-path="' + escapeHtml(row.path) + '" aria-label="Select ' + escapeHtml(row.path) + '" aria-pressed="' + pressed + '">Select</button>' :
      '<span class="hint" title="' + escapeHtml(valid.reason.message) + '">—</span>';
    return '<tr data-row-key="' + escapeHtml(JSON.stringify([row.volume_id,row.binding,row.path_b64 ?? row.path])) + '"' + (stale ? ' style="background:' + ageColor(row.age_days,maxAge) + '"' : '') + '><td class="selectcell">' + button +
      '</td><td class="num sizecell">' + sizeCellHtml(row.bytes,maxBytes) + '</td><td class="num" title="' + fmtDate(row.mtime) + '">' +
      fmtAge(columnValue(route.tab,'age',row,route)) + '</td><td><span class="owner-chip"><span class="owner-dot" style="background:' +
      colorForUid(row.uid) + '"></span>' + escapeHtml(row.owner || ('uid ' + row.uid)) + '</span></td><td><span class="mount-tag">' +
      escapeHtml(volumeLabels[row.mount]) + '</span></td><td class="pathcell">' + pathCellHtml(row.path) + '</td></tr>';
  }
  const viewport = document.getElementById('staleViewport');
  function paint() {
    const rowHeight = 41;
    const start = stale ? Math.min(rows.length,Math.max(0,Math.floor(viewport.scrollTop / rowHeight) - 8)) : 0;
    const end = stale ? Math.min(rows.length,start + Math.ceil(viewport.clientHeight / rowHeight) + 18) : rows.length;
    const spacer = count => count ? '<tr aria-hidden="true" style="height:' + count * rowHeight + 'px"><td colspan="6" style="padding:0"></td></tr>' : '';
    const parsed = document.createElement('tbody');
    parsed.innerHTML = rows.length ? spacer(start) + rows.slice(start,end).map(rowHtml).join('') + spacer(rows.length-end)
      : '<tr><td colspan="6" class="empty">No files match.</td></tr>';
    const previous = new Map(Array.from(tbody.rows, row => [row.dataset.rowKey,row]));
    let position = 0;
    for (const fresh of Array.from(parsed.rows)) {
      const old = fresh.dataset.rowKey && previous.get(fresh.dataset.rowKey);
      const row = old || fresh;
      if (old) {
        const button = row.querySelector('.cleanup-select-btn'), next = fresh.querySelector('.cleanup-select-btn');
        if (button && next) {
          button.className = next.className;
          button.setAttribute('aria-pressed',next.getAttribute('aria-pressed'));
        } else if (button || next) row.cells[0].replaceChildren(...fresh.cells[0].childNodes);
        row.style.background = fresh.style.background;
        for (let i=1;i<row.cells.length;i++) {
          if (!row.cells[i].isEqualNode(fresh.cells[i])) row.cells[i].replaceWith(fresh.cells[i].cloneNode(true));
        }
      }
      const before = tbody.rows[position++] ?? null;
      if (before !== row) tbody.insertBefore(row,before);
    }
    while (tbody.rows.length > position) tbody.deleteRow(-1);
  }
  if (stale) viewport.onscroll = paint;
  paint();
}
