"use strict";

/* =========================================================================
   Users — stacked-by-mount bars + mount filter + expand
   ========================================================================= */
let usersSort = { key: "bytes", dir: -1 };
function userScopeBytes(u) { return userMountFilter ? ((u.by_mount && u.by_mount[userMountFilter]) || 0) : u.bytes; }
function scopeCapacity() { return userMountFilter ? (capUsedByMount[userMountFilter] || 1) : totalCapacityUsed; }

function renderUserMountControls() {
  const seg = document.getElementById("userMountSeg"); seg.innerHTML = "";
  [["", "All"]].concat(mountPaths.map(p => [p, p])).forEach(([val, lab]) => {
    const b = document.createElement("button");
    b.className = (userMountFilter === val ? "active" : ""); b.textContent = lab;
    b.onclick = () => { userMountFilter = val; renderUserMountControls(); renderUsers(); };
    seg.appendChild(b);
  });
  const leg = document.getElementById("userMountLegend"); leg.innerHTML = "";
  (userMountFilter ? [userMountFilter] : mountPaths).forEach(mp => {
    const it = document.createElement("div"); it.className = "legend-item";
    it.innerHTML = '<span class="swatch" style="background:' + mountColor[mp] + '"></span><span class="mono">' + escapeHtml(mp) + '</span>';
    leg.appendChild(it);
  });
}

function renderUsers() {
  const el = document.getElementById("usersChart");
  if (!usersChart) { usersChart = echarts.init(el, null, { renderer: "canvas" }); usersInited = true; }
  const dark = isDark();
  const axisText = dark ? "#98989d" : "#6e6e73", labelText = dark ? "#f5f5f7" : "#1d1d1f", split = dark ? "#2c2c2e" : "#e8e8ed";

  const ranked = [...(DATA.users || [])].map(u => ({ u, scope: userScopeBytes(u) }))
    .filter(x => x.scope > 0).sort((a, b) => b.scope - a.scope);
  const top = ranked.slice(0, 20);
  const names = top.map(x => x.u.name || ("uid " + x.u.uid)).reverse();
  el.style.height = Math.max(320, top.length * 25 + 70) + "px";

  const mountsShown = userMountFilter ? [userMountFilter] : mountPaths;
  const series = mountsShown.map(mp => ({
    name: mp, type: "bar", stack: "u", barMaxWidth: 17,
    itemStyle: { color: mountColor[mp] }, emphasis: { focus: "series" },
    data: top.map(x => (x.u.by_mount && x.u.by_mount[mp]) || 0).reverse()
  }));
  if (series.length) series[series.length - 1].label = {
    show: true, position: "right", color: axisText, fontFamily: "ui-monospace, monospace", fontSize: 11,
    formatter: (p) => humanBytes(top[top.length - 1 - p.dataIndex].scope)
  };

  usersChart.setOption({
    grid: { left: 6, right: 100, top: 8, bottom: 24, containLabel: true },
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" },
      backgroundColor: dark ? "#2c2c2e" : "#fff", borderColor: dark ? "#48484a" : "#d2d2d7", borderWidth: 1,
      textStyle: { color: labelText, fontFamily: "ui-monospace, monospace", fontSize: 12 },
      extraCssText: "border-radius:10px",
      formatter: (params) => {
        const idx = params[0].dataIndex, x = top[top.length - 1 - idx], cap = scopeCapacity();
        let s = '<b>' + escapeHtml(x.u.name) + '</b>  <span style="opacity:.6">uid ' + x.u.uid + '</span><br>';
        s += humanBytes(x.scope) + '  ·  ' + (x.scope / cap * 100).toFixed(1) + '% of ' + (userMountFilter || "all") + '<br>';
        for (const p of params) if (p.value > 0) s += '<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:' +
          p.color + ';margin-right:6px"></span>' + p.seriesName + ' ' + humanBytes(p.value) + '<br>';
        return s;
      }
    },
    xAxis: { type: "value", axisLabel: { color: axisText, fontFamily: "ui-monospace, monospace", formatter: humanBytes },
             splitLine: { lineStyle: { color: split } }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: "category", data: names, axisLabel: { color: labelText, fontFamily: "ui-monospace, monospace", fontSize: 12 },
             axisLine: { show: false }, axisTick: { show: false } },
    series
  }, true);

  usersChart.off("click");
  usersChart.on("click", (p) => { const x = top[top.length - 1 - p.dataIndex]; if (x) switchToUsersRow(x.u.uid); });

  renderUsersTable(); renderUserMountControls();
}

function switchToUsersRow(uid) {
  const row = document.querySelector('#usersTbl tbody tr[data-uid="' + uid + '"]');
  if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
  toggleUserRow(uid);
}

function renderUsersTable() {
  const tbody = document.querySelector("#usersTbl tbody"); tbody.innerHTML = "";
  const cap = scopeCapacity();
  let users = (DATA.users || []).map(u => ({ u, scope: userScopeBytes(u) })).filter(x => x.scope > 0);
  const k = usersSort.key, dir = usersSort.dir;
  users.sort((a, b) => {
    if (k === "name") { const av = (a.u.name || "").toLowerCase(), bv = (b.u.name || "").toLowerCase(); return av < bv ? -dir : av > bv ? dir : 0; }
    if (k === "bytes" || k === "pct") return (a.scope - b.scope) * dir;
    return (((a.u[k]) ?? 0) - ((b.u[k]) ?? 0)) * dir;
  });
  updateArrows("#usersTbl", usersSort);
  if (!users.length) { tbody.innerHTML = '<tr><td colspan="4" class="empty">No user data.</td></tr>'; return; }
  const frag = document.createDocumentFragment();
  for (const { u, scope } of users) {
    const pct = scope / cap * 100;
    const tr = document.createElement("tr");
    tr.dataset.uid = u.uid; tr.style.cursor = "pointer"; tr.tabIndex = 0;
    tr.innerHTML =
      '<td><span class="owner-chip"><span class="owner-dot" style="background:' + colorForUid(u.uid) + '"></span>' +
        escapeHtml(u.name || ("uid " + u.uid)) + ' <span class="hint">uid ' + u.uid + '</span></span></td>' +
      '<td class="num">' + humanBytes(scope) + '</td>' +
      '<td class="num">' + pct.toFixed(1) + '%</td>' +
      '<td class="num">' + (u.files != null ? u.files.toLocaleString() : "—") + '</td>';
    tr.onclick = () => toggleUserRow(u.uid);
    tr.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleUserRow(u.uid); } };
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
  document.getElementById("usersCount").textContent =
    users.length + " user" + (users.length === 1 ? "" : "s") + (userMountFilter ? " on " + userMountFilter : " across all storage");
}

function toggleUserRow(uid) {
  const tbody = document.querySelector("#usersTbl tbody");
  const existing = tbody.querySelector('tr.expand-row[data-for="' + uid + '"]');
  tbody.querySelectorAll("tr.expand-row").forEach(r => r.remove());
  if (existing) return;
  const u = (DATA.users || []).find(x => x.uid === uid);
  const baseRow = tbody.querySelector('tr[data-uid="' + uid + '"]');
  if (!u || !baseRow) return;
  const byMount = u.by_mount || {};
  const max = Math.max(1, ...Object.values(byMount));
  const entries = mountPaths.map(mp => [mp, byMount[mp] || 0]).filter(e => e[1] > 0).sort((a, b) => b[1] - a[1]);
  let inner = '<div class="expand-inner">';
  if (!entries.length) inner += '<span class="hint">No per-storage breakdown.</span>';
  else for (const [mp, b] of entries) inner += '<div class="mini-bar"><span class="mb-label">' + escapeHtml(mp) +
    '</span><span class="mb-track"><span class="mb-fill" style="width:' + (b / max * 100).toFixed(1) + '%;background:' +
    mountColor[mp] + '"></span></span><span class="mb-val">' + humanBytes(b) + '</span><span class="mb-pct">' +
    (b / u.bytes * 100).toFixed(0) + '%</span></div>';
  inner += '</div>';
  const er = document.createElement("tr"); er.className = "expand-row"; er.dataset.for = uid;
  er.innerHTML = '<td colspan="4">' + inner + '</td>';
  baseRow.after(er);
}

/* =========================================================================
   Shared cell builders
   ========================================================================= */
function updateArrows(sel, sort) {
  document.querySelectorAll(sel + " th.sortable, " + sel + " .vc.sortable").forEach(th => {
    const a = th.querySelector(".arrow"); if (a) a.textContent = (th.dataset.key === sort.key) ? (sort.dir < 0 ? "▼" : "▲") : "";
  });
}
function shellQuotePath(path) {
  return "'" + String(path).replace(/'/g, "'\\''") + "'";
}
function buildDeleteCommands(items) {
  return (items || []).filter(x => x && x.path).map(x => "sudo rm -rf -- " + shellQuotePath(x.path)).join("\n");
}
function cleanupItemFromFile(f, source) {
  return {
    path: f.path || "",
    bytes: Number(f.bytes || 0),
    owner: f.owner || ownerByUid.get(f.uid) || "",
    uid: f.uid,
    mount: f.mount || mountOf(f.path || ""),
    source
  };
}
function cleanupCheckboxHtml(f, source) {
  const item = cleanupItemFromFile(f, source);
  const checked = cleanupSelected.has(item.path) ? " checked" : "";
  return '<input type="checkbox" class="cleanup-check" aria-label="Select ' + escapeHtml(item.path) +
    ' for delete command" data-cleanup-source="' + escapeHtml(source) +
    '" data-cleanup-path="' + escapeHtml(item.path) +
    '" data-cleanup-bytes="' + item.bytes +
    '" data-cleanup-owner="' + escapeHtml(item.owner) +
    '" data-cleanup-uid="' + escapeHtml(item.uid == null ? "" : item.uid) +
    '" data-cleanup-mount="' + escapeHtml(item.mount) + '"' + checked + '>';
}
function selectedCleanupItems() {
  return [...cleanupSelected.values()].sort((a, b) => (b.bytes || 0) - (a.bytes || 0) || a.path.localeCompare(b.path));
}
function cleanupItemFromInput(input) {
  return {
    path: input.getAttribute("data-cleanup-path") || "",
    bytes: Number(input.getAttribute("data-cleanup-bytes") || 0),
    owner: input.getAttribute("data-cleanup-owner") || "",
    uid: input.getAttribute("data-cleanup-uid") || "",
    mount: input.getAttribute("data-cleanup-mount") || "",
    source: input.getAttribute("data-cleanup-source") || ""
  };
}
function renderCleanupPanel() {
  const panel = document.getElementById("cleanupPanel");
  if (!panel) return;
  const items = selectedCleanupItems();
  const total = items.reduce((s, x) => s + (x.bytes || 0), 0);
  const commands = buildDeleteCommands(items);
  panel.classList.toggle("show", items.length > 0);
  panel.setAttribute("aria-hidden", items.length ? "false" : "true");
  const summary = document.getElementById("cleanupSummary");
  if (summary) summary.textContent = items.length.toLocaleString() + " selected · " + humanBytes(total);
  const textarea = document.getElementById("cleanupCommands");
  if (textarea) textarea.value = commands;
}
async function copyCleanupCommands() {
  const textarea = document.getElementById("cleanupCommands");
  const btn = document.getElementById("cleanupCopy");
  const text = textarea ? textarea.value : buildDeleteCommands(selectedCleanupItems());
  if (!text) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(text);
    else { textarea.focus(); textarea.select(); document.execCommand("copy"); }
    if (btn) { const old = btn.textContent; btn.textContent = "Copied"; setTimeout(() => btn.textContent = old, 1200); }
  } catch (_) {
    if (btn) btn.textContent = "Copy failed";
  }
}
function clearCleanupSelection() {
  cleanupSelected.clear();
  document.querySelectorAll(".cleanup-check:checked").forEach(cb => { cb.checked = false; });
  renderCleanupPanel();
}
function bindCleanupSelection() {
  document.addEventListener("change", (e) => {
    const input = e.target.closest && e.target.closest(".cleanup-check");
    if (!input) return;
    const item = cleanupItemFromInput(input);
    if (!item.path) return;
    if (input.checked) cleanupSelected.set(item.path, item);
    else cleanupSelected.delete(item.path);
    renderCleanupPanel();
  });
  const copy = document.getElementById("cleanupCopy");
  const clear = document.getElementById("cleanupClear");
  if (copy) copy.onclick = copyCleanupCommands;
  if (clear) clear.onclick = clearCleanupSelection;
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
      '<td class="selectcell">' + cleanupCheckboxHtml(f, "top") + '</td>' +
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
      '<div class="vc vc-select">' + cleanupCheckboxHtml(f, "stale") + '</div>' +
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
