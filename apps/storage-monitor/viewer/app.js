"use strict";

let currentServerId = null;
let currentServerSummary = null;
let currentSession = { authenticated: false, can_rescan: false, csrf_token: "" };
let currentOverviewSummaries = [];
let currentOverviewSnapshotEntries = [];
let currentOverviewRows = [];
let currentDataSource = "static";
let currentDataMode = "inventory";
let staticHostById = new Map();
let snapshotCache = new Map();
let resizeTimer = null;
let rescanTimer = null;
let rescanSawActive = false;
let detailLoadGeneration = 0;
let detailRequestVersions = new Map();
let themeRevealLocked = false;
let overviewLoadGeneration = 0;
let echartsLoadPromise = null;


function readThemeModeCookie() {
  const parts = (document.cookie || "").split("; ");
  const found = parts.find((part) => part.startsWith("themeMode="));
  return found ? found.split("=")[1] : "";
}

function preferredThemeMode() {
  const saved = readThemeModeCookie();
  if (saved === "light" || saved === "dark") return saved;
  if (typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
  return "light";
}

function updateThemeModeButton(mode) {
  const btn = document.getElementById("themeModeButton");
  if (!btn) return;
  const isDark = mode === "dark";
  btn.setAttribute("aria-pressed", isDark ? "true" : "false");
  btn.setAttribute("title", isDark ? "Switch to light mode" : "Switch to dark mode");
}

function applyStoredThemeMode(mode) {
  const next = mode === "light" || mode === "dark" ? mode : preferredThemeMode();
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(next);
  root.dataset.material = "liquid";
  updateThemeModeButton(next);
  return next;
}

function farthestThemeRevealRadius(x, y) {
  const viewportWidth = Math.max(0, Number(window.innerWidth) || 0);
  const viewportHeight = Math.max(0, Number(window.innerHeight) || 0);
  return Math.hypot(
    Math.max(x, viewportWidth - x),
    Math.max(y, viewportHeight - y)
  ) + 24;
}

function themeRectCenter(rect) {
  if (!rect) return null;
  const left = Number(rect.left);
  const top = Number(rect.top);
  const width = Number(rect.width);
  const height = Number(rect.height);
  if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
  return { x: left + width / 2, y: top + height / 2 };
}

function currentThemeIconCenter(button, root) {
  if (!button || typeof button.querySelector !== "function") return null;
  const iconSelector = root.classList.contains("dark") ? ".theme-icon-sun" : ".theme-icon-moon";
  const icon = button.querySelector(iconSelector);
  if (!icon || typeof icon.getBoundingClientRect !== "function") return null;
  return themeRectCenter(icon.getBoundingClientRect());
}

function themeButtonCenter(button) {
  if (!button || typeof button.getBoundingClientRect !== "function") return null;
  return themeRectCenter(button.getBoundingClientRect());
}

function safeThemeRevealCenter() {
  const viewportWidth = Math.max(0, Number(window.innerWidth) || 0);
  return { x: viewportWidth - 28, y: 28 };
}

function refreshThemeDependentVisuals() {
  if (typeof DATA !== "undefined" && DATA && typeof renderTreemap === "function" && currentTab === "treemap") renderTreemap();
  if (typeof usersInited !== "undefined" && usersInited && typeof renderUsers === "function") renderUsers();
}

async function toggleThemeMode(event) {
  const root = document.documentElement;
  if (themeRevealLocked) return root.classList.contains("dark") ? "dark" : "light";
  const next = root.classList.contains("dark") ? "light" : "dark";
  const button = (event && event.currentTarget) || document.getElementById("themeModeButton");
  const reducedMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const supportsViewTransition = typeof document.startViewTransition === "function";
  const applyDestination = () => {
    applyStoredThemeMode(next);
    document.cookie = "themeMode=" + next + "; Path=/; SameSite=Lax";
    refreshThemeDependentVisuals();
  };
  const focusButton = () => {
    if (button && typeof button.focus === "function") button.focus({ preventScroll: true });
  };

  if (reducedMotion || !supportsViewTransition) {
    root.classList.add("theme-no-transition");
    try {
      applyDestination();
      void root.offsetWidth;
      return next;
    } finally {
      root.classList.remove("theme-no-transition");
      focusButton();
    }
  }

  const center = currentThemeIconCenter(button, root) || themeButtonCenter(button) || safeThemeRevealCenter();
  const radius = farthestThemeRevealRadius(center.x, center.y);
  const rootStyle = root.style;
  const setRevealProperty = (name, value) => {
    if (rootStyle && typeof rootStyle.setProperty === "function") rootStyle.setProperty(name, value);
    else if (rootStyle) rootStyle[name] = value;
  };
  const removeRevealProperty = (name) => {
    if (rootStyle && typeof rootStyle.removeProperty === "function") rootStyle.removeProperty(name);
    else if (rootStyle) delete rootStyle[name];
  };

  themeRevealLocked = true;
  root.classList.add("theme-transitioning");
  if (button) button.setAttribute("aria-busy", "true");
  setRevealProperty("--theme-reveal-x", center.x + "px");
  setRevealProperty("--theme-reveal-y", center.y + "px");
  setRevealProperty("--theme-reveal-radius", radius + "px");

  try {
    const transition = document.startViewTransition(() => {
      applyDestination();
    });
    await (transition && transition.finished ? transition.finished : Promise.resolve());
    return next;
  } finally {
    themeRevealLocked = false;
    root.classList.remove("theme-transitioning");
    if (button) {
      if (typeof button.removeAttribute === "function") button.removeAttribute("aria-busy");
      else if (button.attributes) delete button.attributes["aria-busy"];
    }
    removeRevealProperty("--theme-reveal-x");
    removeRevealProperty("--theme-reveal-y");
    removeRevealProperty("--theme-reveal-radius");
    focusButton();
  }
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
  document.body.dataset.shellMode = isDetail ? "detail" : "overview";
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
  const loading = document.getElementById("detailLoading");
  const panels = document.getElementById("detailPanels");
  if (loading) loading.hidden = true;
  if (err) {
    err.textContent = message;
    err.hidden = false;
  }
  if (panels) panels.hidden = true;
}

function clearDetailError() {
  const err = document.getElementById("detailError");
  const loading = document.getElementById("detailLoading");
  const panels = document.getElementById("detailPanels");
  if (err) {
    err.textContent = "";
    err.hidden = true;
  }
  if (panels) panels.hidden = !!(loading && !loading.hidden);
}

function showDetailLoading(summary) {
  const loading = document.getElementById("detailLoading");
  const panels = document.getElementById("detailPanels");
  const caps = document.getElementById("caps");
  const warning = document.getElementById("warnBanner");
  const displayName = summary && summary.display_name ? summary.display_name : "Server";
  if (loading) {
    loading.textContent = displayName + " 데이터를 불러오는 중…";
    loading.hidden = false;
  }
  if (panels) panels.hidden = true;
  if (caps) {
    caps.innerHTML = "";
    caps.hidden = true;
  }
  if (warning) {
    warning.innerHTML = "";
    warning.classList.remove("show");
    warning.hidden = true;
  }
  const host = document.getElementById("h-host");
  const scan = document.getElementById("h-scan");
  const scanner = document.getElementById("h-scanner");
  const lastUpdated = document.getElementById("lastUpd");
  if (host) host.textContent = displayName;
  if (scan) scan.textContent = "—";
  if (scanner) scanner.textContent = "";
  if (lastUpdated) lastUpdated.textContent = "";
}

function clearDetailLoading() {
  const loading = document.getElementById("detailLoading");
  const panels = document.getElementById("detailPanels");
  const caps = document.getElementById("caps");
  const warning = document.getElementById("warnBanner");
  if (loading) {
    loading.textContent = "";
    loading.hidden = true;
  }
  if (panels) panels.hidden = false;
  if (currentServerId) {
    if (caps) caps.hidden = false;
    if (warning) warning.hidden = false;
  }
}

function ensureEchartsLoaded() {
  if (typeof globalThis !== "undefined" && globalThis.echarts) return Promise.resolve(globalThis.echarts);
  if (echartsLoadPromise) return echartsLoadPromise;
  echartsLoadPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "echarts.min.js";
    script.async = true;
    script.onload = () => resolve(globalThis.echarts);
    script.onerror = () => reject(new Error("Could not load the chart renderer."));
    document.head.appendChild(script);
  }).catch((error) => {
    echartsLoadPromise = null;
    throw error;
  });
  return echartsLoadPromise;
}

function renderUsersWhenReady() {
  void ensureEchartsLoaded().then(() => {
    if (DATA && currentTab === "users") renderUsers();
  }).catch((error) => console.error("[storage-viz] failed to load user chart", error));
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
    if (currentTab === "users") { if (!usersInited) renderUsersWhenReady(); else if (usersChart) usersChart.resize(); }
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
  if (typeof resetCleanupSelectionState === "function") resetCleanupSelectionState();
  else if (typeof renderCleanupPanel === "function") renderCleanupPanel();
  renderHeader();
  updateLastUpdated();
  renderMountSeg();
  populateFilters();
  requestAnimationFrame(() => {
    renderTreemap();
    if (currentTab === "users") renderUsersWhenReady();
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

function updateOverviewStatus(message) {
  const el = document.getElementById("overviewStatus");
  if (!el) return;
  const text = message == null ? "" : String(message);
  el.textContent = text;
  el.hidden = text === "";
}

function setSampleDataMarker(dataMode) {
  const marker = document.getElementById("sampleDataMarker");
  if (marker) marker.hidden = dataMode !== "sample";
}

function renderOverview() {
  clearOverviewError();
  currentOverviewRows = buildOverviewRows(currentOverviewSummaries, currentOverviewSnapshotEntries, DEFAULT_CAPACITY_THRESHOLDS);
  const list = document.getElementById("overviewList");
  renderOverviewList(list, currentOverviewRows, { onOpenServer: (serverId) => navigateToServer(serverId) });
  setSampleDataMarker(currentDataMode);
  updateOverviewStatus();
}

async function loadStaticBootstrap() {
  await loadHostManifest();
  staticHostById = new Map(HOSTS.map(host => [host.id, host]));
  const dataMode = HOSTS.length && HOSTS.every(host => host.sample_data === true) ? "sample" : "inventory";
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
    dataMode,
    data_mode: dataMode,
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
  const summaryBody = await summariesLoader();
  const envelope = typeof normalizeServerSummariesEnvelope === "function"
    ? normalizeServerSummariesEnvelope(summaryBody)
    : (Array.isArray(summaryBody) ? { data_mode: "inventory", servers: summaryBody } : { data_mode: "inventory", servers: [] });
  const summaries = envelope.servers || [];
  const snapshots = [];
  const summariesMissingOverview = [];
  for (const summary of summaries) {
    const id = summary && summary.id ? String(summary.id) : "";
    const embedded = summary && summary.overview_snapshot;
    if (embedded && typeof embedded === "object") {
      snapshots.push({ id, snapshot: embedded, error: null, overviewOnly: true });
    } else {
      summariesMissingOverview.push(summary);
    }
  }
  let snapshotTask = null;
  return {
    mode: "api",
    dataMode: envelope.data_mode || "inventory",
    data_mode: envelope.data_mode || "inventory",
    session,
    summaries,
    snapshots,
    startSnapshotLoading(onEntry) {
      if (!snapshotTask) {
        snapshotTask = summariesMissingOverview.length
          ? orderedSnapshotLoader(summariesMissingOverview, snapshotLoader, onEntry)
          : Promise.resolve([]);
      }
      return snapshotTask;
    },
  };
}

async function loadBootstrapData() {
  return loadBootstrapDataWith();
}

function rememberBootstrap(bootstrap) {
  currentDataSource = bootstrap.mode;
  currentDataMode = bootstrap.dataMode || bootstrap.data_mode || "inventory";
  setSampleDataMarker(currentDataMode);
  currentSession = bootstrap.session || { authenticated: false, can_rescan: false, csrf_token: "" };
  currentOverviewSummaries = bootstrap.summaries || [];
  currentOverviewSnapshotEntries = bootstrap.snapshots || [];
  snapshotCache = new Map();
  for (const entry of currentOverviewSnapshotEntries) {
    if (entry && entry.id && entry.snapshot && !entry.overviewOnly) snapshotCache.set(entry.id, entry.snapshot);
  }
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

function startOverviewSnapshotHydration(bootstrap, generation) {
  if (!bootstrap || typeof bootstrap.startSnapshotLoading !== "function") return;
  const detailVersionsAtStart = new Map(detailRequestVersions);
  void bootstrap.startSnapshotLoading((entry) => {
    if (generation !== overviewLoadGeneration || !entry || !entry.id) return;
    if ((detailRequestVersions.get(entry.id) || 0) !== (detailVersionsAtStart.get(entry.id) || 0)) return;
    if (entry.snapshot) snapshotCache.set(entry.id, entry.snapshot);
    updateSnapshotEntry(entry.id, entry.snapshot || null, entry.error || null);
    renderOverview();
  }).catch((error) => {
    if (generation === overviewLoadGeneration) console.error("[storage-viz] failed to hydrate overview snapshots", error);
  });
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

function beginDetailRequestVersion(serverId) {
  const next = (detailRequestVersions.get(serverId) || 0) + 1;
  detailRequestVersions.set(serverId, next);
  return next;
}

function isCurrentDetailRequestVersion(serverId, version) {
  return detailRequestVersions.get(serverId) === version;
}

async function ensureDetailLoaded(serverId, forceReload, generationOverride, requestVersionOverride) {
  const generation = generationOverride == null ? detailLoadGeneration : generationOverride;
  const requestVersion = requestVersionOverride == null ? beginDetailRequestVersion(serverId) : requestVersionOverride;
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
      if (!isCurrentDetailRequestVersion(serverId, requestVersion)) return;
      snapshotCache.set(serverId, snapshot);
      updateSnapshotEntry(serverId, snapshot, null);
      renderOverview();
    } catch (error) {
      if (!isCurrentDetailRequestVersion(serverId, requestVersion)) return;
      console.error("[storage-viz] failed to load snapshot", serverId, error);
      updateSnapshotEntry(serverId, null, error);
      renderOverview();
      if (isCurrentDetailGeneration(serverId, generation)) showDetailError("Could not load snapshot for " + summary.display_name + ".");
      return;
    }
  }
  if (!isCurrentDetailGeneration(serverId, generation)) return;
  DATA = snapshot;
  clearDetailLoading();
  clearDetailError();
  renderAll();
  updateLastUpdated();
  syncRescanButton();
}

function applyRouteState(route, options) {
  const opts = options || {};
  const safeRoute = {
    serverId: route && route.serverId && isSafeServerId(route.serverId) ? route.serverId : null,
    tab: route && route.tab ? route.tab : "treemap",
  };
  if (!opts.skipHistory) syncHistory(safeRoute, !!opts.replaceHistory);
  detailLoadGeneration += 1;
  currentServerId = safeRoute.serverId;
  currentServerSummary = safeRoute.serverId ? (currentOverviewSummaries.find(item => item.id === safeRoute.serverId) || null) : null;
  DATA = safeRoute.serverId ? (snapshotCache.get(safeRoute.serverId) || null) : null;
  if (typeof resetCleanupSelectionState === "function") resetCleanupSelectionState();
  clearDetailError();
  setShellMode(!!safeRoute.serverId);
  syncRescanButton();
  if (!safeRoute.serverId) {
    clearDetailLoading();
    return safeRoute;
  }
  if (!DATA) showDetailLoading(currentServerSummary);
  showTab(safeRoute.tab, { updateRoute: false });
  if (!opts.skipDataLoad) {
    const requestVersion = beginDetailRequestVersion(safeRoute.serverId);
    void ensureDetailLoaded(safeRoute.serverId, !!opts.forceReload, detailLoadGeneration, requestVersion);
  }
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
  const generation = ++overviewLoadGeneration;
  try {
    const bootstrap = await loadBootstrapData();
    if (generation !== overviewLoadGeneration) return;
    rememberBootstrap(bootstrap);
    renderOverview();
    startOverviewSnapshotHydration(bootstrap, generation);
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
  applyStoredThemeMode();
  const themeButton = document.getElementById("themeModeButton");
  if (themeButton) themeButton.onclick = toggleThemeMode;
  const initialRoute = currentRoute();
  const generation = ++overviewLoadGeneration;
  let bootstrap;
  try {
    bootstrap = await loadBootstrapData();
  } catch (error) {
    console.error("[storage-viz] failed to initialize overview", error);
    showOverviewError("Overview data is unavailable.");
    navigateToOverview({ skipHistory: true });
    return;
  }
  if (generation !== overviewLoadGeneration) return;
  rememberBootstrap(bootstrap);
  renderOverview();
  startOverviewSnapshotHydration(bootstrap, generation);
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
      if (document.body && document.body.dataset.shellMode === "overview" && typeof scheduleOverviewMasonry === "function") scheduleOverviewMasonry(document.getElementById("overviewList"));
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
  return {
    currentServerId,
    currentServerSummary,
    data: DATA,
    detailLoadGeneration,
    detailRequestVersions: new Map(detailRequestVersions),
  };
}

function getOverviewModeDebugState() {
  return {
    dataSource: currentDataSource,
    dataMode: currentDataMode,
  };
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
  getOverviewModeDebugState,
  applyStoredThemeMode,
  toggleThemeMode,
});
if (typeof module !== "undefined" && module.exports) module.exports = {
  loadBootstrapDataWith,
  shouldFallbackToStatic,
};
