"use strict";

let currentServerId = null;
let currentServerSummary = null;
let currentSession = { authenticated: false, can_rescan: false, csrf_token: "" };
let currentOverviewSummaries = [];
let currentOverviewSnapshotEntries = [];
let currentOverviewRows = [];
let currentDataSource = "static";
let staticHostById = new Map();
let snapshotCache = new Map();
let resizeTimer = null;
let rescanTimer = null;
let rescanSawActive = false;
let detailLoadGeneration = 0;

/* =========================================================================
   Wiring
   ========================================================================= */
function bindSort(sel, sortState, rerender) {
  document.querySelectorAll(sel + " th.sortable, " + sel + " .vc.sortable").forEach(th => {
    th.tabIndex = 0;
    const act = () => {
      const key = th.dataset.key;
      if (sortState.key === key) sortState.dir *= -1;
      else sortState.key = key, sortState.dir = (key === "path" || key === "owner" || key === "name" || key === "mount") ? 1 : -1;
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

function syncHistory(route, replace) {
  if (!window.history) return;
  const href = buildRouteHref(window.location.pathname, route);
  if (replace) window.history.replaceState(route, "", href);
  else window.history.pushState(route, "", href);
}

function setShellMode(isDetail) {
  const overviewView = document.getElementById("overviewView");
  const detailView = document.getElementById("detailView");
  const back = document.getElementById("overviewBack");
  const detailActions = document.getElementById("detailActions");
  const detailHead = document.getElementById("detailHead");
  const warnBanner = document.getElementById("warnBanner");
  const caps = document.getElementById("caps");
  const detailTabs = document.getElementById("detailTabs");
  if (overviewView) overviewView.hidden = !!isDetail;
  if (detailView) detailView.hidden = !isDetail;
  if (back) back.hidden = !isDetail;
  if (detailActions) detailActions.hidden = !isDetail;
  if (detailHead) detailHead.hidden = !isDetail;
  if (warnBanner) warnBanner.hidden = !isDetail;
  if (caps) caps.hidden = !isDetail;
  if (detailTabs) detailTabs.hidden = !isDetail;
  const subtitle = document.getElementById("brandSubtitle");
  if (subtitle) subtitle.textContent = isDetail && currentServerSummary
    ? currentServerSummary.display_name + " 상세 보기"
    : "고정 순서 서버 저장소 개요";
}

function showDetailError(message) {
  const err = document.getElementById("detailError");
  const panels = document.getElementById("detailPanels");
  if (err) {
    err.textContent = message;
    err.hidden = false;
  }
  if (panels) panels.hidden = true;
}

function clearDetailError() {
  const err = document.getElementById("detailError");
  const panels = document.getElementById("detailPanels");
  if (err) {
    err.textContent = "";
    err.hidden = true;
  }
  if (panels) panels.hidden = false;
}

function showTab(name, options) {
  const updateRoute = !options || options.updateRoute !== false;
  currentTab = KNOWN_DETAIL_TABS.has(name) ? name : "treemap";
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === currentTab));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === "panel-" + currentTab));
  if (updateRoute && currentServerId) syncHistory({ serverId: currentServerId, tab: currentTab }, true);
  requestAnimationFrame(() => {
    if (!DATA) return;
    if (currentTab === "treemap") renderTreemap();
    if (currentTab === "users") { if (!usersInited) renderUsers(); else if (usersChart) usersChart.resize(); }
    if (currentTab === "stale") renderStaleWindow();
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
  renderHeader();
  updateLastUpdated();
  renderMountSeg();
  populateFilters();
  requestAnimationFrame(() => {
    renderTreemap();
    renderTopFiles();
    prepStale();
  });
}

function showOverviewError(message) {
  const err = document.getElementById("overviewError");
  if (err) {
    err.textContent = message;
    err.hidden = false;
  }
}

function clearOverviewError() {
  const err = document.getElementById("overviewError");
  if (err) {
    err.textContent = "";
    err.hidden = true;
  }
}

function updateOverviewStatus() {
  const el = document.getElementById("overviewStatus");
  if (el) el.textContent = currentOverviewRows.length + " servers";
}

function renderOverview() {
  clearOverviewError();
  currentOverviewRows = buildOverviewRows(currentOverviewSummaries, currentOverviewSnapshotEntries, DEFAULT_CAPACITY_THRESHOLDS);
  const list = document.getElementById("overviewList");
  renderOverviewList(list, currentOverviewRows, { onOpenServer: (serverId) => navigateToServer(serverId) });
  updateOverviewStatus();
}

async function loadStaticBootstrap() {
  await loadHostManifest();
  staticHostById = new Map(HOSTS.map(host => [host.id, host]));
  const summaries = HOSTS.map((host, index) => ({
    id: host.id,
    display_name: host.label,
    order: index,
    mount_count: 0,
    snapshot_availability: "available",
    freshness: "fresh",
    latest_pull_status: "succeeded",
    latest_scan_result: "complete",
    configuration_sync: "in_sync",
    active_job: null,
  }));
  const snapshots = await loadOrderedSnapshotsForOverview(summaries, async (serverId) => {
    const host = staticHostById.get(serverId);
    if (!host) throw new Error("unknown static host");
    return loadHost(host);
  });
  for (const row of summaries) {
    const entry = snapshots.find(item => item.id === row.id);
    if (entry && entry.snapshot && Array.isArray(entry.snapshot.mounts)) row.mount_count = entry.snapshot.mounts.length;
  }
  return {
    mode: "static",
    session: { authenticated: false, can_rescan: false, csrf_token: "" },
    summaries,
    snapshots,
  };
}

function shouldFallbackToStatic(error) {
  return !!(error && error.status === 404);
}

async function loadBootstrapDataWith(loaders) {
  const deps = loaders || {};
  const sessionLoader = deps.loadSession || loadSession;
  const summariesLoader = deps.loadServerSummaries || loadServerSummaries;
  const orderedSnapshotLoader = deps.loadOrderedSnapshotsForOverview || loadOrderedSnapshotsForOverview;
  const staticBootstrapLoader = deps.loadStaticBootstrap || loadStaticBootstrap;
  const snapshotLoader = deps.loadServerSnapshot || loadServerSnapshot;
  let session;
  try {
    session = await sessionLoader();
  } catch (error) {
    if (!shouldFallbackToStatic(error)) throw error;
    console.warn("[storage-viz] API session probe returned 404; using static data", error);
    return staticBootstrapLoader();
  }
  staticHostById = new Map();
  const summaries = await summariesLoader();
  return {
    mode: "api",
    session,
    summaries,
    snapshots: await orderedSnapshotLoader(summaries, snapshotLoader),
  };
}

async function loadBootstrapData() {
  return loadBootstrapDataWith();
}

function rememberBootstrap(bootstrap) {
  currentDataSource = bootstrap.mode;
  currentSession = bootstrap.session || { authenticated: false, can_rescan: false, csrf_token: "" };
  currentOverviewSummaries = bootstrap.summaries || [];
  currentOverviewSnapshotEntries = bootstrap.snapshots || [];
  snapshotCache = new Map();
  for (const entry of currentOverviewSnapshotEntries) if (entry && entry.id && entry.snapshot) snapshotCache.set(entry.id, entry.snapshot);
}

function updateSnapshotEntry(serverId, snapshot, error) {
  let found = false;
  currentOverviewSnapshotEntries = currentOverviewSnapshotEntries.map((entry) => {
    if (entry.id !== serverId) return entry;
    found = true;
    return { id: serverId, snapshot, error: error || null };
  });
  if (!found) currentOverviewSnapshotEntries.push({ id: serverId, snapshot, error: error || null });
}

async function loadSnapshotForCurrentSource(serverId) {
  if (currentDataSource === "api") return loadServerSnapshot(serverId);
  const host = staticHostById.get(serverId);
  if (!host) throw new Error("unknown host");
  return loadHost(host);
}

function currentDetailLoader() {
  if (typeof globalThis !== "undefined" && typeof globalThis.loadSnapshotForCurrentSource === "function") return globalThis.loadSnapshotForCurrentSource;
  return loadSnapshotForCurrentSource;
}

function currentRoute() {
  return parseRoute(window.location);
}

function isCurrentDetailGeneration(serverId, generation) {
  return currentServerId === serverId && detailLoadGeneration === generation;
}

async function ensureDetailLoaded(serverId, forceReload, generationOverride) {
  const generation = generationOverride == null ? detailLoadGeneration : generationOverride;
  const summary = currentOverviewSummaries.find(item => item.id === serverId);
  currentServerSummary = summary || null;
  if (!summary) {
    if (isCurrentDetailGeneration(serverId, generation)) showDetailError("Unknown server: " + serverId);
    return;
  }
  let snapshot = !forceReload ? snapshotCache.get(serverId) : null;
  if (!snapshot) {
    try {
      snapshot = await currentDetailLoader()(serverId);
      snapshotCache.set(serverId, snapshot);
      updateSnapshotEntry(serverId, snapshot, null);
      renderOverview();
    } catch (error) {
      console.error("[storage-viz] failed to load snapshot", serverId, error);
      updateSnapshotEntry(serverId, null, error);
      renderOverview();
      if (isCurrentDetailGeneration(serverId, generation)) showDetailError("Could not load snapshot for " + summary.display_name + ".");
      return;
    }
  }
  if (!isCurrentDetailGeneration(serverId, generation)) return;
  DATA = snapshot;
  clearDetailError();
  renderAll();
  updateLastUpdated();
  syncRescanButton();
}

function applyRouteState(route, options) {
  const opts = options || {};
  const safeRoute = {
    serverId: route && route.serverId && SAFE_SERVER_ID_RE.test(route.serverId) ? route.serverId : null,
    tab: route && route.tab ? route.tab : "treemap",
  };
  if (!opts.skipHistory) syncHistory(safeRoute, !!opts.replaceHistory);
  detailLoadGeneration += 1;
  currentServerId = safeRoute.serverId;
  currentServerSummary = safeRoute.serverId ? (currentOverviewSummaries.find(item => item.id === safeRoute.serverId) || null) : null;
  DATA = safeRoute.serverId ? (snapshotCache.get(safeRoute.serverId) || null) : null;
  clearDetailError();
  setShellMode(!!safeRoute.serverId);
  if (!safeRoute.serverId) return safeRoute;
  showTab(safeRoute.tab, { updateRoute: false });
  if (!opts.skipDataLoad) void ensureDetailLoaded(safeRoute.serverId, !!opts.forceReload, detailLoadGeneration);
  return safeRoute;
}

function navigateToServer(serverId, options) {
  const opts = options || {};
  return applyRouteState({ serverId, tab: opts.tab || currentTab || "treemap" }, {
    skipHistory: !!opts.skipHistory,
    replaceHistory: !!opts.replaceHistory,
    skipDataLoad: !!opts.skipDataLoad,
    forceReload: !!opts.forceReload,
  });
}

function navigateToOverview(options) {
  const opts = options || {};
  return applyRouteState({ serverId: null, tab: "treemap" }, {
    skipHistory: !!opts.skipHistory,
    replaceHistory: !!opts.replaceHistory,
    skipDataLoad: true,
  });
}

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

function setRescanUnsupported(btn, message) {
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = "Manual rescan";
  btn.title = message || "Manual rescan only: run scanner separately and refresh.";
}

function syncRescanButton() {
  const btn = document.getElementById("rescanBtn");
  if (!btn) return;
  if (!currentServerId || currentDataSource !== "api") return setRescanUnsupported(btn, "Manual rescan only in API-backed mode.");
  if (!currentSession.can_rescan) return setRescanUnsupported(btn, "Read-only session: manual rescan is disabled.");
  btn.disabled = false;
  btn.textContent = "↻ Rescan";
  btn.title = "Run a fresh scan now";
}

function clearRescanPoll() {
  if (rescanTimer) {
    clearInterval(rescanTimer);
    rescanTimer = null;
  }
}

async function refreshOverviewData(options) {
  const opts = options || {};
  const route = currentRoute();
  try {
    const bootstrap = await loadBootstrapData();
    rememberBootstrap(bootstrap);
    renderOverview();
    if (route.serverId) {
      navigateToServer(route.serverId, { skipHistory: true, tab: route.tab, forceReload: !!opts.forceReload });
    } else {
      navigateToOverview({ skipHistory: true });
    }
  } catch (error) {
    console.error("[storage-viz] failed to refresh overview", error);
    showOverviewError("Overview refresh failed.");
  }
}

async function pollRescanJob() {
  const btn = document.getElementById("rescanBtn");
  if (!btn || !currentServerId || currentDataSource !== "api") return;
  try {
    const body = await loadServerJob(currentServerId);
    const job = body && body.job;
    if (job && (job.state === "requested" || job.state === "running")) {
      rescanSawActive = true;
      btn.disabled = true;
      btn.textContent = job.state === "requested" ? "⟳ Queued…" : "⟳ Scanning…";
      if (!rescanTimer) rescanTimer = setInterval(() => { void pollRescanJob(); }, 2000);
      return;
    }
    clearRescanPoll();
    syncRescanButton();
    if (rescanSawActive) {
      rescanSawActive = false;
      await refreshOverviewData({ forceReload: true });
    }
  } catch (error) {
    clearRescanPoll();
    setRescanUnsupported(btn, "Rescan status unavailable.");
  }
}

async function triggerRescan() {
  const btn = document.getElementById("rescanBtn");
  if (!btn || !currentServerId || currentDataSource !== "api" || !currentSession.can_rescan) return;
  btn.disabled = true;
  btn.textContent = "⟳ Starting…";
  try {
    await postServerRescan(currentServerId, currentSession.csrf_token);
  } catch (error) {
    setRescanUnsupported(btn, (error && error.body && error.body.error) || "Rescan unavailable.");
    return;
  }
  await pollRescanJob();
}

async function init() {
  const initialRoute = currentRoute();
  let bootstrap;
  try {
    bootstrap = await loadBootstrapData();
  } catch (error) {
    console.error("[storage-viz] failed to initialize overview", error);
    showOverviewError("Overview data is unavailable.");
    navigateToOverview({ skipHistory: true });
    return;
  }
  rememberBootstrap(bootstrap);
  renderOverview();
  bindSort("#usersTbl", usersSort, renderUsersTable);
  bindSort("#topTbl", topSort, renderTopFiles);
  bindSort("#staleHead", staleSort, renderStale);
  bindCopy();
  bindCleanupSelection();
  if (typeof bindTreemapCleanupMode === "function") bindTreemapCleanupMode();

  document.querySelectorAll(".tab").forEach(t => { t.onclick = () => showTab(t.dataset.tab); });
  const back = document.getElementById("overviewBack");
  if (back) back.onclick = () => navigateToOverview();

  (function () {
    const tmEl = document.getElementById("treemap");
    const tip = document.getElementById("tmtip");
    let last = null;
    if (!tmEl || !tip) return;
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
      const hl = (tile && !tile.classList.contains("tmgroup")) ? tile : null;
      if (hl !== last) { if (last) last.classList.remove("tmhover"); if (hl) hl.classList.add("tmhover"); last = hl; }
      if (tile && tile._tip) showTip(tile._tip, e.clientX, e.clientY);
      else tip.classList.remove("show");
    });
    tmEl.addEventListener("mouseleave", () => { if (last) { last.classList.remove("tmhover"); last = null; } tip.classList.remove("show"); });
  })();

  document.getElementById("ownerFilter").onchange = (e) => { topOwnerFilter = e.target.value; renderTopFiles(); };
  document.getElementById("staleOwnerFilter").onchange = (e) => { staleOwnerFilter = e.target.value; renderStale(); };
  document.getElementById("staleViewport").addEventListener("scroll", () => requestAnimationFrame(renderStaleWindow), { passive: true });

  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (currentTab === "treemap") renderTreemap();
      if (currentTab === "users" && usersChart) usersChart.resize();
      if (currentTab === "stale") renderStaleWindow();
    }, 150);
  });
  window.addEventListener("popstate", () => {
    const route = currentRoute();
    applyRouteState(route, { skipHistory: true });
  });

  if (window.ResizeObserver) {
    let roTimer = null;
    const ro = new ResizeObserver(() => {
      clearTimeout(roTimer);
      roTimer = setTimeout(() => { if (currentTab === "treemap") renderTreemap(); }, 60);
    });
    ro.observe(document.getElementById("main"));
  }
  window.addEventListener("load", () => { if (currentTab === "treemap") renderTreemap(); });
  setTimeout(() => { if (currentTab === "treemap") renderTreemap(); }, 400);

  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onScheme = () => { if (DATA) renderTreemap(); if (usersInited) renderUsers(); };
    if (mq.addEventListener) mq.addEventListener("change", onScheme); else if (mq.addListener) mq.addListener(onScheme);
  }

  const rb = document.getElementById("rescanBtn");
  if (rb) rb.onclick = triggerRescan;
  syncRescanButton();
  setInterval(updateLastUpdated, 30000);

  if (initialRoute.serverId) navigateToServer(initialRoute.serverId, { skipHistory: true, tab: initialRoute.tab });
  else navigateToOverview({ skipHistory: true });
}

if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", init);

function getCurrentDetailDebugState() {
  return { currentServerId, currentServerSummary, data: DATA, detailLoadGeneration };
}

if (typeof globalThis !== "undefined") Object.assign(globalThis, {
  applyRouteState,
  navigateToOverview,
  navigateToServer,
  rememberBootstrap,
  ensureDetailLoaded,
  loadBootstrapDataWith,
  loadSnapshotForCurrentSource,
  getCurrentDetailDebugState,
});
if (typeof module !== "undefined" && module.exports) module.exports = {
  loadBootstrapDataWith,
  shouldFallbackToStatic,
};
