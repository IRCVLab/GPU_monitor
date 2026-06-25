"use strict";
/* =========================================================================
   Shared cell builders
   ========================================================================= */
function updateArrows(sel, sort) {
  document.querySelectorAll(sel + " th.sortable, " + sel + " .vc.sortable").forEach(th => {
    const a = th.querySelector(".arrow"); if (a) a.textContent = (th.dataset.key === sort.key) ? (sort.dir < 0 ? "▼" : "▲") : "";
  });
}
function pathCellHtml(path) {
  const idx = path.lastIndexOf("/");
  const dir = idx >= 0 ? path.slice(0, idx + 1) : "", file = idx >= 0 ? path.slice(idx + 1) : path;
  return '<div class="pathwrap"><span class="pathtext" title="' + escapeHtml(path) + '"><bdi>' +
    '<span class="dir">' + escapeHtml(dir) + '</span><span class="file">' + escapeHtml(file) + '</span></bdi></span>' +
    '<button class="copybtn" data-copy="' + escapeHtml(path) + '" title="Copy full path">Copy</button></div>';
}
function sizeCellHtml(bytes, maxBytes) {
  const w = maxBytes > 0 ? Math.max(2, bytes / maxBytes * 100) : 0;
  return '<span class="size-bar" style="width:' + w.toFixed(1) + '%"></span><span class="size-val">' + humanBytes(bytes) + '</span>';
}
function ownerCellHtml(uid, owner) {
  return '<span class="owner-chip"><span class="owner-dot" style="background:' + colorForUid(uid) + '"></span>' +
    escapeHtml(owner || ("uid " + uid)) + '</span>';
}
function sortRows(rows, sort) {
  const k = sort.key, dir = sort.dir;
  rows.sort((a, b) => {
    let av = a[k], bv = b[k];
    if (k === "path" || k === "owner" || k === "mount") { av = (av || "").toLowerCase(); bv = (bv || "").toLowerCase(); return av < bv ? -dir : av > bv ? dir : 0; }
    return ((av ?? 0) - (bv ?? 0)) * dir;
  });
}

/* ---- Top files (200 rows — plain DOM, fine) ---- */
let topSort = { key: "bytes", dir: -1 }, topOwnerFilter = "";
let topRowsCache = null;
function topRowsAll() {
  if (!topRowsCache) topRowsCache = (DATA.top_files || []).map(f => ({ ...f, age: daysAgo(f.mtime), mount: mountOf(f.path) }));
  return topRowsCache;
}
function renderTopFiles() {
  const tbody = document.querySelector("#topTbl tbody"); tbody.innerHTML = "";
  let rows = topRowsAll();
  if (topOwnerFilter) rows = rows.filter(f => String(f.uid) === topOwnerFilter);
  if (topMountFilter) rows = rows.filter(f => f.mount === topMountFilter);
  rows = rows.slice();
  const maxBytes = rows.reduce((m, f) => Math.max(m, f.bytes || 0), 0);
  sortRows(rows, topSort); updateArrows("#topTbl", topSort);
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="6" class="empty">No files match.</td></tr>'; document.getElementById("topCount").textContent = ""; return; }
  const frag = document.createDocumentFragment();
  for (const f of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td class="select-col">' + selectionCellHtml(f, 'top_files') + '</td>' +
      '<td class="num sizecell">' + sizeCellHtml(f.bytes, maxBytes) + '</td>' +
      '<td class="num" title="' + fmtDate(f.mtime) + '">' + fmtAge(f.age) + '</td>' +
      '<td>' + ownerCellHtml(f.uid, f.owner) + '</td>' +
      '<td><span class="mount-tag">' + escapeHtml(f.mount) + '</span></td>' +
      '<td class="pathcell">' + pathCellHtml(f.path) + '</td>';
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
  document.getElementById("topCount").textContent = rows.length + " files · " +
    humanBytes(rows.reduce((s, f) => s + (f.bytes || 0), 0)) + " total";
}

/* ---- Stale (~4000 rows — VIRTUALIZED) ---- */
let staleSort = { key: "bytes", dir: -1 }, staleOwnerFilter = "";
let staleRowsCache = null, staleFiltered = [], staleMaxBytes = 0, staleMaxAge = 1;
const VROW_H = 41, VBUFFER = 8;
function ageColor(age, maxAge) {
  const t = Math.min(1, (age || 0) / (maxAge || 1));
  return "rgba(255,59,48," + (0.04 + t * 0.16).toFixed(3) + ")";
}
function staleRowsAll() {
  if (!staleRowsCache) staleRowsCache = (DATA.stale || []).map(f => ({ ...f, mount: mountOf(f.path) }));
  return staleRowsCache;
}
function prepStale() {
  let rows = staleRowsAll();
  if (staleOwnerFilter) rows = rows.filter(f => String(f.uid) === staleOwnerFilter);
  if (staleMountFilter) rows = rows.filter(f => f.mount === staleMountFilter);
  rows = rows.slice();
  sortRows(rows, staleSort);
  staleFiltered = rows;
  staleMaxBytes = rows.reduce((m, f) => Math.max(m, f.bytes || 0), 0);
  staleMaxAge = rows.reduce((m, f) => Math.max(m, f.age_days || 0), 1);
  const reclaim = rows.reduce((s, f) => s + (f.bytes || 0), 0);
  document.getElementById("staleCaption").innerHTML =
    'Big, long-untouched files — the prime <b>cleanup candidates</b>. Deleting everything listed would reclaim ' +
    '<span class="reclaim">' + humanBytes(reclaim) + '</span>' + (staleMountFilter ? ' on ' + escapeHtml(staleMountFilter) : '') +
    '. Sorted by size; rows tint redder with age. Verify ownership before deleting.';
  document.getElementById("staleCount").textContent = rows.length.toLocaleString() + " files · " + humanBytes(reclaim) + " reclaimable";
  // spacer height = full dataset; only the window is in the DOM
  document.getElementById("staleSpacer").style.height = (rows.length * VROW_H) + "px";
  updateArrows("#staleHead", staleSort);
}
function renderStaleWindow() {
  const vp = document.getElementById("staleViewport");
  const rowsEl = document.getElementById("staleRows");
  const total = staleFiltered.length;
  if (!total) { rowsEl.innerHTML = '<div class="empty">No stale files match.</div>'; document.getElementById("staleSpacer").style.height = "0px"; return; }
  const scrollTop = vp.scrollTop, h = vp.clientHeight || 500;
  let start = Math.max(0, Math.floor(scrollTop / VROW_H) - VBUFFER);
  let end = Math.min(total, Math.ceil((scrollTop + h) / VROW_H) + VBUFFER);
  rowsEl.style.transform = "translateY(" + (start * VROW_H) + "px)";
  let html = "";
  for (let i = start; i < end; i++) {
    const f = staleFiltered[i];
    html += '<div class="vrow" style="background:' + ageColor(f.age_days, staleMaxAge) + '">' +
      '<div class="vc vc-select">' + selectionCellHtml(f, 'stale') + '</div>' +
      '<div class="vc vc-size sizecell num">' + sizeCellHtml(f.bytes, staleMaxBytes) + '</div>' +
      '<div class="vc vc-age num" title="' + fmtDate(f.mtime) + '">' + fmtAge(f.age_days) + '</div>' +
      '<div class="vc vc-own">' + ownerCellHtml(f.uid, f.owner) + '</div>' +
      '<div class="vc vc-mnt"><span class="mount-tag">' + escapeHtml(f.mount) + '</span></div>' +
      '<div class="vc vc-path">' + pathCellHtml(f.path) + '</div></div>';
  }
  rowsEl.innerHTML = html;
}
function renderStale() { prepStale(); document.getElementById("staleViewport").scrollTop = 0; renderStaleWindow(); }

/* ---- filter controls ---- */
function populateFilters() {
  const fillOwner = (selId, list) => {
    const sel = document.getElementById(selId); const cur = sel.value;
    sel.innerHTML = '<option value="">All</option>';
    const seen = new Map();
    for (const f of list) if (!seen.has(f.uid)) seen.set(f.uid, f.owner || ("uid " + f.uid));
    [...seen.entries()].sort((a, b) => (a[1] || "").localeCompare(b[1] || "")).forEach(([uid, name]) => {
      const o = document.createElement("option"); o.value = String(uid); o.textContent = name; sel.appendChild(o);
    });
    sel.value = cur;
  };
  fillOwner("ownerFilter", DATA.top_files || []);
  fillOwner("staleOwnerFilter", DATA.stale || []);
  const fillMountSeg = (segId, getV, setV, rerender) => {
    const seg = document.getElementById(segId);
    const render = () => {
      seg.innerHTML = "";
      [["", "All"]].concat(mountPaths.map(p => [p, p])).forEach(([val, lab]) => {
        const b = document.createElement("button"); b.className = (getV() === val ? "active" : ""); b.textContent = lab;
        b.onclick = () => { setV(val); render(); rerender(); }; seg.appendChild(b);
      });
    };
    render();
  };
  fillMountSeg("topMountSeg", () => topMountFilter, v => topMountFilter = v, renderTopFiles);
  fillMountSeg("staleMountSeg", () => staleMountFilter, v => staleMountFilter = v, renderStale);
}


