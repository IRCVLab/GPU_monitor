"use strict";
/* =========================================================================
   storage-viz — Apple-style, performance-minded viewer.
   - Parse JSON once; build the ECharts treemap data for the CURRENT mount
     only, on demand. Dispose/rebuild on mount/host switch.
   - Defer chart init until the Treemap tab is first visible; Users chart
     inits lazily on first Users-tab activation.
   - Stale (~4000 rows) is virtualized: only the visible window is in the DOM.
   - Fully offline; everything is JSON-driven.
   ========================================================================= */

let HOSTS = [];

/* ---- Utilities ---- */
function humanBytes(n) {
  if (n == null || isNaN(n)) return "—";
  if (n < 1024) return n + " B";
  const u = ["KB", "MB", "GB", "TB", "PB"]; // 1024-based magnitudes, short labels
  let i = -1, v = n;
  do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
  return v.toFixed(v < 10 ? 2 : (v < 100 ? 1 : 0)) + " " + u[i];
}
function fmtDate(unix) {
  if (!unix) return "—";
  const d = new Date(unix * 1000);
  if (isNaN(d.getTime())) return "—";
  const p = (x) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}
function fmtAge(days) {
  if (days == null) return "—";
  if (days < 1) return "today";
  if (days < 30) return days + "d";
  if (days < 365) return (days / 30).toFixed(days < 90 ? 1 : 0) + "mo";
  return (days / 365).toFixed(1) + "y";
}
function daysAgo(unix) { return unix ? Math.max(0, Math.round((nowUnix - unix) / 86400)) : null; }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function mountOf(path) {
  let best = "/";
  for (const mp of mountPaths) {
    if (mp === "/") continue;
    if ((path === mp || path.startsWith(mp + "/")) && mp.length > best.length) best = mp;
  }
  return best;
}

/* ---- Color: stable uid palette + fixed per-mount palette ----
   Harmonious, desaturated Apple-like categorical set: evenly-spaced hues at a
   uniform saturation/lightness (~S52% L56%) so owners read as one calm family
   inside the light card (and stay legible in dark), with clear hue separation. */
const PALETTE = [
  "#548fc9", "#5463c9", "#7254c9", "#9d54c9", "#c954c9",
  "#c9549d", "#c95472", "#c96354", "#c98f54", "#c9bb54",
  "#acc954", "#80c954", "#54c954", "#54c980", "#54c9ac",
  "#54bbc9"
];
const OTHER_COLOR = "#9aa0a6";

/* Pick the label color that maximizes WCAG contrast against the tile color —
   white vs near-black, whichever wins. (A fixed luminance cutoff mis-handles
   mid-tone tiles, e.g. our desaturated greens read far better with dark text.) */
function relLum(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255,
        g = parseInt(hex.slice(3, 5), 16) / 255,
        b = parseInt(hex.slice(5, 7), 16) / 255;
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
const DARK_LABEL = "#1d1d1f";
function labelColorForBg(hex) {
  if (!hex || hex[0] !== "#" || hex.length < 7) return "#ffffff";
  const L = relLum(hex), Ldark = relLum(DARK_LABEL);
  const cWhite = (1.0 + 0.05) / (L + 0.05);          // contrast vs white
  const cDark = (L + 0.05) / (Ldark + 0.05);         // contrast vs near-black
  return cWhite >= cDark ? "#ffffff" : DARK_LABEL;
}
/* per-mount colors: same calm family, hues spread for clear separation */
const MOUNT_PALETTE = ["#548fc9", "#9d54c9", "#54c954", "#c98f54", "#c9549d", "#54c9ac"];
let mountColor = {};
const uidColorMap = new Map();
let uidCursor = 0;
function colorForUid(uid) {
  if (uid == null) return OTHER_COLOR;
  if (uidColorMap.has(uid)) return uidColorMap.get(uid);
  const c = uidCursor < PALETTE.length ? PALETTE[uidCursor++] : OTHER_COLOR;
  uidColorMap.set(uid, c); return c;
}
function assignColors(users) {
  uidColorMap.clear(); uidCursor = 0;
  [...(users || [])].sort((a, b) => b.bytes - a.bytes).forEach(u => colorForUid(u.uid));
  if (!uidColorMap.has(0)) colorForUid(0);
}

/* ---- State ---- */
let DATA = null;
let ownerByUid = new Map();
let nowUnix = Math.floor(Date.now() / 1000);
let treemapChart = null, usersChart = null;
let treemapInited = false, usersInited = false;
let currentMountIdx = 0;
let mountPaths = [];
let capUsedByMount = {};
let totalCapacityUsed = 1;
let currentTab = "treemap";
let userMountFilter = "", topMountFilter = "", staleMountFilter = "";
const uiWarnings = { manifest: null, data: null, rescan: null };
function setUiWarning(key, message) {
  uiWarnings[key] = message || null;
  if (DATA) renderHeader();
}

/* =========================================================================
   Header + capacity hero (rendered immediately for fast first paint)
   ========================================================================= */
function capColor(p) { return p >= 90 ? "var(--crit)" : p >= 75 ? "var(--warn)" : "var(--ok)"; }
function renderHeader() {
  document.getElementById("h-host").textContent = DATA.hostname || "—";
  document.getElementById("h-scan").textContent = fmtDate(DATA.scan_started_unix) +
    (DATA.scan_duration_sec ? " · " + DATA.scan_duration_sec.toFixed(0) + "s" : "");
  document.getElementById("h-scanner").textContent =
    "scanner v" + (DATA.scanner_version || "?") + (DATA.run_as_root ? " · root scan" : " · non-root");

  const warn = document.getElementById("warnBanner");
  const msgs = Object.values(uiWarnings).filter(Boolean);
  if (DATA.run_as_root === false) msgs.push("run_as_root=false → some directories were not scanned and are hidden from totals.");
  const nB = (DATA.blocked || []).length;
  if (nB > 0) {
    const list = DATA.blocked.slice(0, 6).map(b => b.path + " (" + b.reason + ")").join(", ");
    msgs.push(nB + " director" + (nB === 1 ? "y" : "ies") + " could not be read: " + escapeHtml(list) + (nB > 6 ? " …" : ""));
  }
  warn.classList.toggle("show", msgs.length > 0);
  warn.innerHTML = msgs.length ? "⚠ " + msgs.join("<br>⚠ ") : "";

  const caps = document.getElementById("caps");
  caps.innerHTML = ""; totalCapacityUsed = 0; capUsedByMount = {};
  for (const m of DATA.mounts || []) {
    capUsedByMount[m.path] = m.df_used || 0; totalCapacityUsed += (m.df_used || 0);
    const pct = (m.df_use_pct != null) ? m.df_use_pct : (m.df_total ? Math.round(m.df_used / m.df_total * 100) : 0);
    const color = capColor(pct);
    const div = document.createElement("div");
    div.className = "cap";
    div.innerHTML =
      '<div class="cap-top"><span><span class="cap-path">' + escapeHtml(m.path) + '</span>' +
        '<span class="cap-fs">' + escapeHtml(m.fstype || "") + '</span></span>' +
        '<span class="cap-pct" style="color:' + color + '">' + pct + '<span style="font-size:15px">%</span></span></div>' +
      '<div class="cap-bar"><div class="cap-fill" style="width:' + Math.min(100, pct) + '%;background:' + color + '"></div></div>' +
      '<div class="cap-sub"><span class="figure">' + humanBytes(m.df_used) + '</span> used / <span class="figure">' +
        humanBytes(m.df_total) + '</span> · <span class="figure">' + humanBytes(m.df_avail) + '</span> free</div>';
    caps.appendChild(div);
  }
  if (totalCapacityUsed <= 0) totalCapacityUsed = 1;
}

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
  refreshSelectionChecks();
  assignColors(DATA.users);
  ownerByUid = new Map();
  for (const u of DATA.users || []) ownerByUid.set(u.uid, u.name);
  for (const f of (DATA.top_files || []).concat(DATA.stale || []))
    if (f.uid != null && f.owner && !ownerByUid.has(f.uid)) ownerByUid.set(f.uid, f.owner);
  mountPaths = (DATA.mounts || []).map(m => m.path);
  mountColor = {}; mountPaths.forEach((p, i) => mountColor[p] = MOUNT_PALETTE[i % MOUNT_PALETTE.length]);
  currentMountIdx = 0; userMountFilter = topMountFilter = staleMountFilter = "";
  topRowsCache = null; staleRowsCache = null;

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
  clearSelectedPaths();
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

/* ---- Last-updated label + on-demand rescan (server runs the scan as root) ---- */
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
let rescanTimer = null, sawScanRunning = false, rescanSupported = false;
function setRescanUnavailable(message) {
  const btn = document.getElementById("rescanBtn"); if (!btn) return;
  rescanSupported = false;
  btn.disabled = true;
  btn.textContent = "Manual rescan only";
  btn.title = message || "This server is serving static files and cannot start scans.";
}
async function pollRescan() {
  let st;
  try {
    const r = await fetch("/rescan-status", { cache: "no-store" });
    if (!r.ok) throw new Error("/rescan-status -> " + r.status);
    st = await r.json();
  } catch (e) {
    console.info("[storage-viz] rescan status unavailable:", e);
    setRescanUnavailable("Manual rescan only: /rescan-status is unavailable on this server.");
    return;
  }
  const btn = document.getElementById("rescanBtn"); if (!btn) return;
  if (st.supported === false) {
    setRescanUnavailable(st.message || "Manual rescan only: server-side rescan is disabled.");
    return;
  }
  rescanSupported = true;
  setUiWarning("rescan", st.error ? "Last rescan failed: " + st.error : null);
  if (st.scanning) {
    sawScanRunning = true;
    const secs = Math.max(0, Math.floor(Date.now() / 1000 - st.started));
    btn.disabled = true; btn.textContent = "⟳ Scanning… " + secs + "s";
    if (!rescanTimer) rescanTimer = setInterval(pollRescan, 2000);
  } else {
    btn.disabled = false; btn.textContent = "↻ Rescan"; btn.title = "Run a fresh scan now";
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
  if (!rescanSupported) { setRescanUnavailable("Manual rescan only: server-side rescan is disabled."); return; }
  btn.disabled = true; btn.textContent = "⟳ Starting…";
  try {
    const r = await fetch("/rescan", { method: "POST" });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || ("/rescan -> " + r.status));
    sawScanRunning = true;
    setUiWarning("rescan", null);
  } catch (e) {
    console.error("[storage-viz] failed to start rescan", e);
    setUiWarning("rescan", "Could not start rescan: " + String(e.message || e));
    btn.disabled = false; btn.textContent = "Rescan failed"; btn.title = String(e.message || e);
  }
  pollRescan();
}

let resizeTimer = null;
async function init() {
  const sel = document.getElementById("hostSel");
  HOSTS = await loadHostManifest();
  HOSTS.forEach(h => { const o = document.createElement("option"); o.value = h.id; o.textContent = h.label; sel.appendChild(o); });
  const def = HOSTS.find(h => h.default) || HOSTS[0];
  if (def) sel.value = def.id;
  sel.onchange = () => { const h = HOSTS.find(x => x.id === sel.value); if (h) selectHost(h); };

  document.querySelectorAll(".tab").forEach(t => { t.onclick = () => showTab(t.dataset.tab); });
  bindSort("#usersTbl", usersSort, renderUsersTable);
  bindSort("#topTbl", topSort, renderTopFiles);
  bindSort("#staleHead", staleSort, renderStale);
  bindCopy();
  initSelectionPanel();

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
  // recompute the DOM treemap height to fit inside it. This prevents the chart
  // from being taller than the visible area (bottom clip).
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

  if (def) selectHost(def);
}
document.addEventListener("DOMContentLoaded", init);
