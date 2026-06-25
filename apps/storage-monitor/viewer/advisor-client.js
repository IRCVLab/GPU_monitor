"use strict";

/* Optional AI Advisor client/state. Static viewer safe: failed fetches become disabled UI. */
const ADVISOR_EXCLUSIONS_KEY = "storage-viz.aiAdvisor.exclusions.v1";
var advisorState = (typeof globalThis !== "undefined" && globalThis.advisorState) || {
  status: { enabled: false, message: "AI Advisor not checked yet.", provider: "", model: "qwen3.6:27b" },
  payload: null,
  recommendations: [],
  exclusions: [],
  running: false,
  error: null,
};

function advisorNowUnix() { return Math.floor(Date.now() / 1000); }

function normalizeAdvisorExclusions(value) {
  const rows = Array.isArray(value) ? value : (value && Array.isArray(value.items) ? value.items : []);
  return rows.filter(item => item && typeof item === "object" && typeof item.type === "string");
}

function advisorStorageKey(hostId) {
  return ADVISOR_EXCLUSIONS_KEY + ":" + String(hostId || "default");
}

function loadAdvisorExclusions(hostId) {
  if (typeof localStorage === "undefined") return [];
  try {
    const parsed = JSON.parse(localStorage.getItem(advisorStorageKey(hostId)) || "[]");
    advisorState.exclusions = normalizeAdvisorExclusions(parsed);
  } catch (_) {
    advisorState.exclusions = [];
  }
  return advisorState.exclusions;
}

function saveAdvisorExclusions(hostId, exclusions) {
  advisorState.exclusions = normalizeAdvisorExclusions(exclusions);
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(advisorStorageKey(hostId), JSON.stringify(advisorState.exclusions));
  }
  return advisorState.exclusions;
}

function normalizeAdvisorPath(path) {
  return String(path || "").replace(/\/+$/g, "") || "/";
}

function advisorPathMatches(path, base) {
  const p = normalizeAdvisorPath(path);
  const b = normalizeAdvisorPath(base);
  return p === b || p.startsWith(b + "/");
}

function simpleGlobToRegExp(pattern) {
  const escaped = String(pattern || "").replace(/[.+^${}()|[\]\\]/g, "\\$&");
  return new RegExp("^" + escaped.replace(/\*\*/g, ".*").replace(/\*/g, "[^/]*") + "$");
}

function recommendationExcluded(rec, exclusions) {
  const paths = [rec && rec.target_path].concat((rec && rec.related_paths) || []).filter(Boolean).map(String);
  for (const ex of normalizeAdvisorExclusions(exclusions)) {
    if (ex.type === "recommendation" && ex.id === rec.id) return true;
    if (ex.type === "action" && ex.action === rec.action) return true;
    if (ex.type === "category" && ex.category === rec.category) return true;
    if (ex.type === "path" && ex.path && paths.some(path => advisorPathMatches(path, ex.path))) return true;
    if (ex.type === "pattern" && ex.pattern) {
      const re = simpleGlobToRegExp(ex.pattern);
      if (paths.some(path => re.test(path))) return true;
    }
  }
  return false;
}

function filterExcludedRecommendations(recommendations, exclusions) {
  return (Array.isArray(recommendations) ? recommendations : []).filter(rec => !recommendationExcluded(rec, exclusions));
}


function normalizeAdvisorPayload(payload, exclusions) {
  const source = payload && typeof payload === "object" ? payload : {};
  const out = Object.assign({ schema_version: 1, host_id: currentAdvisorHostId(), summary: null, recommendations: [] }, source);
  out.recommendations = filterExcludedRecommendations(Array.isArray(source.recommendations) ? source.recommendations : [], normalizeAdvisorExclusions(exclusions));
  return out;
}


function advisorOutputLanguage(payload) {
  if (payload && payload.output_language) return String(payload.output_language).toLowerCase();
  const text = JSON.stringify([payload && payload.summary, payload && payload.recommendations]);
  return /[가-힣]/.test(text) ? "ko" : "unknown";
}

function groupAdvisorRecommendations(recommendations) {
  const order = ["delete", "move", "dedupe", "archive", "investigate", "keep"];
  const map = new Map();
  for (const rec of Array.isArray(recommendations) ? recommendations : []) {
    const action = String(rec && rec.action || "investigate");
    if (!map.has(action)) map.set(action, { action, recommendations: [], bytes: 0, priority: "low" });
    const group = map.get(action);
    group.recommendations.push(rec);
    group.bytes += Number(rec && rec.bytes) || 0;
    if (["critical", "high", "medium", "low"].indexOf(rec.priority) < ["critical", "high", "medium", "low"].indexOf(group.priority)) group.priority = rec.priority;
  }
  return Array.from(map.values()).sort((a, b) => {
    const ai = order.indexOf(a.action), bi = order.indexOf(b.action);
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
}

function advisorActionLabel(action) {
  return { delete: "삭제 검토", move: "HDD 이동", dedupe: "중복 확인", archive: "보관 검토", investigate: "확인 필요", keep: "유지" }[action] || "검토";
}

function advisorPathDepth(path) {
  return String(path || "").split("/").filter(Boolean).length;
}
function isDeleteSelectableRecommendation(rec) {
  return !!rec && rec.action === "delete" && rec.suggested_next_step === "review-delete-command" && typeof rec.target_path === "string" && rec.target_path.startsWith("/") && !rec.target_path.includes("\0") && advisorPathDepth(rec.target_path) > 1;
}

function advisorSidecarBaseUrl() {
  if (typeof globalThis !== "undefined" && globalThis.STORAGE_VIZ_AI_BASE_URL) {
    return String(globalThis.STORAGE_VIZ_AI_BASE_URL).replace(/\/+$/, "");
  }
  try {
    if (typeof localStorage !== "undefined") {
      const configured = localStorage.getItem("storage-viz.aiAdvisor.baseUrl");
      if (configured) return configured.replace(/\/+$/, "");
    }
  } catch (_) {}
  if (typeof location !== "undefined" && location.protocol && location.hostname) {
    const port = String(location.port || (location.protocol === "https:" ? "443" : "80"));
    const fallbackPorts = ["18089", "18088"];
    const urls = fallbackPorts.filter(p => p !== port).map(p => location.protocol + "//" + location.hostname + ":" + p);
    return urls.join(",");
  }
  return "";
}

function advisorApiUrls(path) {
  const urls = [path];
  const sidecar = advisorSidecarBaseUrl();
  if (sidecar) {
    for (const base of sidecar.split(",").map(s => s.trim()).filter(Boolean)) urls.push(base + path);
  }
  return Array.from(new Set(urls));
}

async function fetchAdvisorJson(path, options) {
  let lastError = null;
  for (const url of advisorApiUrls(path)) {
    try {
      const response = await fetch(url, options);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        lastError = new Error((body && (body.error || body.message)) || (url + " -> " + response.status));
        continue;
      }
      return { body, url };
    } catch (e) {
      lastError = e;
    }
  }
  throw lastError || new Error("AI Advisor endpoint is unavailable");
}

async function fetchAdvisorStatus() {
  try {
    const result = await fetchAdvisorJson("/ai/status", { cache: "no-store" });
    advisorState.status = result.body;
    advisorState.error = null;
    advisorState.apiBaseUrl = result.url.endsWith("/ai/status") ? result.url.slice(0, -"/ai/status".length) : "";
  } catch (e) {
    advisorState.status = { enabled: false, provider: "static", model: "", message: "AI Advisor unavailable: " + String(e && e.message ? e.message : e) };
    advisorState.error = null;
  }
  if (typeof renderAdvisorPanel === "function") renderAdvisorPanel();
  return advisorState.status;
}

function currentAdvisorHostId() {
  const sel = typeof document !== "undefined" ? document.getElementById("hostSel") : null;
  return sel && sel.value ? sel.value : (typeof HOSTS !== "undefined" && HOSTS[0] ? HOSTS[0].id : "hinton");
}

async function runAdvisor(options) {
  const opts = options || {};
  const hostId = opts.hostId || currentAdvisorHostId();
  loadAdvisorExclusions(hostId);
  advisorState.running = true;
  advisorState.error = null;
  if (typeof renderAdvisorPanel === "function") renderAdvisorPanel();
  try {
    const result = await fetchAdvisorJson("/ai/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host_id: hostId,
        exclusions: advisorState.exclusions,
        language: opts.language || "ko",
        max_items: opts.max_items || 50,
        force_refresh: !!opts.force_refresh,
      }),
    });
    const body = result.body;
    advisorState.apiBaseUrl = result.url.endsWith("/ai/recommend") ? result.url.slice(0, -"/ai/recommend".length) : "";
    advisorState.payload = body;
    advisorState.recommendations = filterExcludedRecommendations(body.recommendations || [], advisorState.exclusions);
    advisorState.error = body.advisor_error || null;
  } catch (e) {
    advisorState.error = String(e && e.message ? e.message : e);
    advisorState.payload = null;
    advisorState.recommendations = [];
  } finally {
    advisorState.running = false;
    if (typeof renderAdvisorPanel === "function") renderAdvisorPanel();
    if (typeof refreshAdvisorBadges === "function") refreshAdvisorBadges();
  }
  return advisorState.payload;
}

function addAdvisorExclusion(hostId, exclusion) {
  const rows = loadAdvisorExclusions(hostId);
  const item = { ...(exclusion || {}), created_at: advisorNowUnix() };
  if (!item.type) return rows;
  const key = JSON.stringify(item, Object.keys(item).sort());
  const existing = new Set(rows.map(row => JSON.stringify(row, Object.keys(row).sort())));
  if (!existing.has(key)) rows.push(item);
  saveAdvisorExclusions(hostId, rows);
  if (advisorState.payload) advisorState.recommendations = filterExcludedRecommendations(advisorState.payload.recommendations || [], rows);
  if (typeof renderAdvisorPanel === "function") renderAdvisorPanel();
  if (typeof refreshAdvisorBadges === "function") refreshAdvisorBadges();
  return rows;
}

function clearAdvisorExclusions(hostId) {
  saveAdvisorExclusions(hostId, []);
  if (advisorState.payload) advisorState.recommendations = advisorState.payload.recommendations || [];
  if (typeof renderAdvisorPanel === "function") renderAdvisorPanel();
  if (typeof refreshAdvisorBadges === "function") refreshAdvisorBadges();
}

if (typeof globalThis !== "undefined") {
  Object.assign(globalThis, {
    advisorState,
    advisorSidecarBaseUrl,
    advisorApiUrls,
    fetchAdvisorJson,
    fetchAdvisorStatus,
    runAdvisor,
    loadAdvisorExclusions,
    saveAdvisorExclusions,
    addAdvisorExclusion,
    clearAdvisorExclusions,
    filterExcludedRecommendations,
    recommendationExcluded,
    normalizeAdvisorPayload,
    advisorOutputLanguage,
    groupAdvisorRecommendations,
    advisorActionLabel,
    isDeleteSelectableRecommendation,
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    advisorState,
    normalizeAdvisorExclusions,
    filterExcludedRecommendations,
    recommendationExcluded,
    normalizeAdvisorPayload,
    advisorOutputLanguage,
    groupAdvisorRecommendations,
    advisorActionLabel,
    isDeleteSelectableRecommendation,
    loadAdvisorExclusions,
    saveAdvisorExclusions,
    addAdvisorExclusion,
    advisorSidecarBaseUrl,
    advisorApiUrls,
    fetchAdvisorJson,
    fetchAdvisorStatus,
    runAdvisor,
  };
}
