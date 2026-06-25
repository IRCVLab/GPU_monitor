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
  const rows = Array.isArray(value) ? value : [];
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

function isDeleteSelectableRecommendation(rec) {
  return !!rec && rec.action === "delete" && rec.suggested_next_step === "review-delete-command" && typeof rec.target_path === "string" && rec.target_path.startsWith("/");
}

async function fetchAdvisorStatus() {
  try {
    const response = await fetch("/ai/status", { cache: "no-store" });
    if (!response.ok) throw new Error("/ai/status -> " + response.status);
    advisorState.status = await response.json();
    advisorState.error = null;
  } catch (e) {
    advisorState.status = { enabled: false, provider: "static", model: "", message: "AI Advisor unavailable in static mode." };
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
    const response = await fetch("/ai/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host_id: hostId,
        exclusions: advisorState.exclusions,
        language: opts.language || "en",
        max_items: opts.max_items || 50,
        force_refresh: !!opts.force_refresh,
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || ("/ai/recommend -> " + response.status));
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
    fetchAdvisorStatus,
    runAdvisor,
    loadAdvisorExclusions,
    saveAdvisorExclusions,
    addAdvisorExclusion,
    clearAdvisorExclusions,
    filterExcludedRecommendations,
    recommendationExcluded,
    isDeleteSelectableRecommendation,
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    advisorState,
    normalizeAdvisorExclusions,
    filterExcludedRecommendations,
    recommendationExcluded,
    isDeleteSelectableRecommendation,
    loadAdvisorExclusions,
    saveAdvisorExclusions,
    addAdvisorExclusion,
  };
}
