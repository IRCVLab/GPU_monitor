"use strict";

/* =========================================================================
   Wiring
   ========================================================================= */
function bindSort(sel, sortState, rerender) {
  document.querySelectorAll(sel + " th.sortable, " + sel + " .vc.sortable").forEach(th => {
    th.tabIndex = 0;
    const act = () => {
      const key = th.dataset.key;
      if (sortState.key === key) sortState.dir *= -1;
      else { sortState.key = key; sortState.dir = (key === "path" || key === "owner" || key === "name" || key === "mount") ? 1 : -1; }
      rerender();
    };
    th.onclick = act;
    th.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(); } };
  });
}
function bindCopy() {
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".copybtn"); if (!btn) return;
    e.stopPropagation();
    const text = btn.getAttribute("data-copy");
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(text);
      else { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
      const old = btn.textContent; btn.textContent = "Copied"; btn.classList.add("done");
      setTimeout(() => { btn.textContent = old; btn.classList.remove("done"); }, 1200);
    } catch (_) { btn.textContent = "Failed"; }
  });
}

function showTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === "panel-" + name));
  requestAnimationFrame(() => {
    if (name === "treemap") renderTreemap();
    if (name === "users") { if (!usersInited) renderUsers(); else if (usersChart) usersChart.resize(); }
    if (name === "stale") renderStaleWindow();
  });
}

function renderAll() {
  assignColors(DATA.users);
  ownerByUid = new Map();
  for (const u of DATA.users || []) ownerByUid.set(u.uid, u.name);
  for (const f of (DATA.top_files || []).concat(DATA.stale || []))
    if (f.uid != null && f.owner && !ownerByUid.has(f.uid)) ownerByUid.set(f.uid, f.owner);
  mountPaths = (DATA.mounts || []).map(m => m.path);
  mountColor = {}; mountPaths.forEach((p, i) => mountColor[p] = MOUNT_PALETTE[i % MOUNT_PALETTE.length]);
  currentMountIdx = 0; userMountFilter = topMountFilter = staleMountFilter = "";
  topRowsCache = null; staleRowsCache = null;
  cleanupSelected.clear(); renderCleanupPanel();

  // Fast first paint: header + hero + controls now.
  renderHeader();
  updateLastUpdated();
  renderMountSeg();
  populateFilters();

  // Defer the heavier work so the hero shows immediately.
  requestAnimationFrame(() => {
    renderTreemap();           // builds only the active mount's tree
    renderTopFiles();          // 200 rows
    prepStale();               // computes filtered set + caption; window paints on tab show
    // users chart inits lazily on first Users-tab activation
  });
}

async function selectHost(host) {
  // dispose charts on host switch (frees ECharts instances)
  treemapStack = [];
  if (usersChart) { usersChart.dispose(); usersChart = null; usersInited = false; }
  try { DATA = await loadHost(host); }
  catch (e) {
    console.error("[storage-viz] failed to load host", host.id, e);
    document.getElementById("main").innerHTML = '<div class="err">Could not load data for <b>' + escapeHtml(host.label) +
      '</b>.<br>Tried <code>data/' + escapeHtml(host.file) + '.json</code> and <code>.sample.json</code>.<br>' + escapeHtml(String(e)) + '</div>';
    return;
  }
  renderAll();
}

/* ---- Last-updated label + optional server-side rescan ---- */
function fmtRel(unix) {
  if (!unix) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000) - unix);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return Math.floor(s / 86400) + " d ago";
}
function updateLastUpdated() {
  const el = document.getElementById("lastUpd"); if (!el) return;
  if (DATA && DATA.scan_started_unix) {
    el.innerHTML = "Updated <b>" + fmtRel(DATA.scan_started_unix) + "</b>";
    el.title = "Last scan: " + fmtDate(DATA.scan_started_unix);
  } else el.textContent = "";
}
let rescanTimer = null, sawScanRunning = false;
function setRescanUnsupported(btn, message) {
  btn.disabled = true;
  btn.textContent = "Manual rescan";
  btn.title = message || "Manual rescan only: run scanner separately and refresh.";
}
async function pollRescan() {
  let st;
  try { st = await (await fetch("/rescan-status", { cache: "no-store" })).json(); }
  catch (e) { return; }   // endpoint not available (e.g. plain static server)
  const btn = document.getElementById("rescanBtn"); if (!btn) return;
  if (st.supported === false) {
    setRescanUnsupported(btn, st.message);
    return;
  }
  if (st.scanning) {
    sawScanRunning = true;
    const secs = Math.max(0, Math.floor(Date.now() / 1000 - st.started));
    btn.disabled = true; btn.textContent = "⟳ Scanning… " + secs + "s";
    if (!rescanTimer) rescanTimer = setInterval(pollRescan, 2000);
  } else {
    btn.disabled = false; btn.textContent = "↻ Rescan";
    if (rescanTimer) { clearInterval(rescanTimer); rescanTimer = null; }
    if (sawScanRunning) {            // a scan we were watching just finished → reload fresh data
      sawScanRunning = false;
      if (st.error) console.error("[storage-viz] scan error:", st.error);
      const h = HOSTS.find(x => x.id === document.getElementById("hostSel").value) || HOSTS[0];
      if (h) await selectHost(h);
    }
  }
}
async function triggerRescan() {
  const btn = document.getElementById("rescanBtn");
  btn.disabled = true; btn.textContent = "⟳ Starting…";
  try {
    const r = await fetch("/rescan", { method: "POST" });
    if (r.status === 503) {
      const body = await r.json().catch(() => ({}));
      setRescanUnsupported(btn, body.error || body.message);
      return;
    }
  } catch (e) {}
  sawScanRunning = true;
  pollRescan();
}

let resizeTimer = null;
async function init() {
  await loadHostManifest();
  const sel = document.getElementById("hostSel");
  sel.innerHTML = "";
  HOSTS.forEach(h => { const o = document.createElement("option"); o.value = h.id; o.textContent = h.label; sel.appendChild(o); });
  sel.onchange = () => { const h = HOSTS.find(x => x.id === sel.value); if (h) selectHost(h); };

  document.querySelectorAll(".tab").forEach(t => { t.onclick = () => showTab(t.dataset.tab); });
  bindSort("#usersTbl", usersSort, renderUsersTable);
  bindSort("#topTbl", topSort, renderTopFiles);
  bindSort("#staleHead", staleSort, renderStale);
  bindCopy();
  bindCleanupSelection();
  if (typeof bindTreemapCleanupMode === "function") bindTreemapCleanupMode();

  // Hover-highlight EXACTLY the topmost tile under the cursor (e.target is the
  // top element), so a parent group is never highlighted by mistake.
  (function () {
    const tmEl = document.getElementById("treemap");
    const tip = document.getElementById("tmtip");
    let last = null;
    const showTip = (d, x, y) => {
      tip.innerHTML =
        '<div class="tip-path">' + escapeHtml(d.other ? d.path.replace(/\/[^/]*$/, "") + "/ (other small files)" : d.path) + '</div>' +
        '<div class="tip-size">' + humanBytes(d.bytes) + '</div>' +
        '<div class="tip-meta"><span class="tip-dot" style="background:' + d.color + '"></span>' +
          (d.other ? 'aggregated small files (below threshold)' : 'owner <b>' + escapeHtml(d.owner) + '</b>') +
          (d.files != null ? ' · <b>' + d.files.toLocaleString() + '</b> files' : '') +
          (d.mtime ? ' · modified <b>' + fmtDate(d.mtime) + '</b>' : '') + '</div>';
      tip.classList.add("show");
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let nx = x + 16, ny = y + 16;
      if (nx + w > window.innerWidth - 8) nx = x - w - 16;
      if (ny + h > window.innerHeight - 8) ny = y - h - 16;
      tip.style.left = Math.max(8, nx) + "px"; tip.style.top = Math.max(8, ny) + "px";
    };
    tmEl.addEventListener("mousemove", (e) => {
      const tile = e.target.closest(".tmtile");
      const hl = (tile && !tile.classList.contains("tmgroup")) ? tile : null; // highlight leaves only
      if (hl !== last) { if (last) last.classList.remove("tmhover"); if (hl) hl.classList.add("tmhover"); last = hl; }
      if (tile && tile._tip) showTip(tile._tip, e.clientX, e.clientY);
      else tip.classList.remove("show");
    });
    tmEl.addEventListener("mouseleave", () => { if (last) { last.classList.remove("tmhover"); last = null; } tip.classList.remove("show"); });
  })();

  document.getElementById("ownerFilter").onchange = (e) => { topOwnerFilter = e.target.value; renderTopFiles(); };
  document.getElementById("staleOwnerFilter").onchange = (e) => { staleOwnerFilter = e.target.value; renderStale(); };

  // virtualized stale: re-render window on scroll
  document.getElementById("staleViewport").addEventListener("scroll", () => {
    requestAnimationFrame(renderStaleWindow);
  }, { passive: true });

  // debounced resize
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (currentTab === "treemap") renderTreemap();
      if (currentTab === "users" && usersChart) usersChart.resize();
      if (currentTab === "stale") renderStaleWindow();
    }, 150);
  });

  // Observe the scroll container (main): whenever its size settles/changes
  // (header height resolving, caps wrapping, font load, window resize, zoom),
  // recompute the treemap height to fit inside it and resize the canvas. This is
  // what prevents the chart from being taller than the visible area (bottom clip).
  if (window.ResizeObserver) {
    let roTimer = null;
    const ro = new ResizeObserver(() => {
      clearTimeout(roTimer);
      roTimer = setTimeout(() => { if (currentTab === "treemap") renderTreemap(); }, 60);
    });
    ro.observe(document.getElementById("main"));
  }
  // One more recompute after fonts/layout fully settle (covers first paint).
  window.addEventListener("load", () => { if (currentTab === "treemap") renderTreemap(); });
  setTimeout(() => { if (currentTab === "treemap") renderTreemap(); }, 400);

  // re-theme charts when the OS switches light/dark
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onScheme = () => { if (DATA) renderTreemap(); if (usersInited) renderUsers(); };
    if (mq.addEventListener) mq.addEventListener("change", onScheme); else if (mq.addListener) mq.addListener(onScheme);
  }

  const rb = document.getElementById("rescanBtn");
  if (rb) rb.onclick = triggerRescan;
  pollRescan();                          // reflect a scan already running (started by another viewer)
  setInterval(pollRescan, 5000);         // stay in sync with other viewers
  setInterval(updateLastUpdated, 30000); // keep "x min ago" fresh

  if (HOSTS.length) selectHost(HOSTS[0]);
}
document.addEventListener("DOMContentLoaded", init);
