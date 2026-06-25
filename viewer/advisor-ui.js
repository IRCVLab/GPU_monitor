"use strict";

/* =========================================================================
   AI Advisor UI — status, run, details, exclusions, cleanup handoff
   ========================================================================= */
const advisorState = {
  host: null,
  data: null,
  status: Object.assign({}, typeof ADVISOR_DISABLED_STATUS !== "undefined" ? ADVISOR_DISABLED_STATUS : { enabled: false, provider: "disabled", model: "", message: "AI Advisor unavailable" }),
  exclusions: { version: 1, host_id: "default", items: [] },
  rawPayload: null,
  payload: null,
  selectedRecommendationId: "",
  running: false,
  error: "",
};

function advisorHtml(value) {
  if (typeof escapeHtml === "function") return escapeHtml(value);
  return String(value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function advisorBytes(value) {
  return typeof humanBytes === "function" ? humanBytes(value) : String(value || 0) + " B";
}
function currentAdvisorHostId() {
  return typeof advisorHostId === "function" ? advisorHostId(advisorState.host) : ((advisorState.host && advisorState.host.id) || "default");
}
function getAdvisorRecommendation(id) {
  const rows = advisorState.payload && advisorState.payload.recommendations || [];
  return rows.find(rec => rec.id === id) || null;
}
function advisorIsAbsolutePath(path) {
  if (typeof isAbsoluteCleanupPath === "function") return isAbsoluteCleanupPath(path);
  return typeof path === "string" && path.startsWith("/") && !path.includes("\0");
}
function advisorPathDepth(path) {
  return String(path || "").split("/").filter(Boolean).length;
}
function isAdvisorSafeDeleteRecommendation(rec) {
  if (!rec) return false;
  if (rec.action !== "delete" || rec.suggested_next_step !== "review-delete-command") return false;
  if (!advisorIsAbsolutePath(rec.target_path)) return false;
  if (typeof isTopLevelCleanupPath === "function" && isTopLevelCleanupPath(rec.target_path)) return false;
  if (typeof isTopLevelCleanupPath !== "function" && advisorPathDepth(rec.target_path) <= 1) return false;
  return true;
}
function advisorHealthClass() {
  const health = String(advisorState.payload && advisorState.payload.summary && advisorState.payload.summary.health || "ok");
  return health === "critical" ? " critical" : health === "warning" ? " warning" : " ok";
}
function advisorStatusLabel() {
  if (advisorState.running) return "running";
  if (advisorState.error) return "failed";
  if (!advisorState.status.enabled) return "disabled";
  return advisorState.status.cached ? "cached" : (advisorState.status.mode || advisorState.status.provider || "ready");
}
function advisorApplyFilteredPayload() {
  advisorState.payload = typeof normalizeAdvisorPayload === "function"
    ? normalizeAdvisorPayload(advisorState.rawPayload || {}, advisorState.exclusions)
    : (advisorState.rawPayload || { recommendations: [] });
  if (typeof advisorSetRecommendations === "function") advisorSetRecommendations(advisorState.payload);
  return advisorState.payload;
}
function advisorSelectDeleteCandidate(id) {
  const rec = getAdvisorRecommendation(id);
  if (!isAdvisorSafeDeleteRecommendation(rec)) return false;
  if (typeof setCleanupSelectedItem !== "function") return false;
  const ok = setCleanupSelectedItem({
    path: rec.target_path,
    bytes: Number(rec.bytes) || 0,
    owner: rec.owner || "",
    source: "ai-advisor",
  }, true);
  if (typeof renderCleanupPanel === "function") renderCleanupPanel();
  return ok;
}
function renderAdvisorSummary() {
  const summary = advisorState.payload && advisorState.payload.summary;
  if (!summary) return '<div class="advisor-empty">Run the advisor to see evidence-backed storage recommendations. Static viewers show a disabled status until a local AI endpoint is enabled.</div>';
  const drivers = Array.isArray(summary.top_drivers) ? summary.top_drivers : [];
  return '<div class="advisor-summary' + advisorHealthClass() + '">' +
    '<div><span class="advisor-health">' + advisorHtml(summary.health || "ok") + '</span><h3>' + advisorHtml(summary.headline || "AI Advisor summary") + '</h3></div>' +
    (drivers.length ? '<ul>' + drivers.slice(0, 4).map(d => '<li>' + advisorHtml(d) + '</li>').join("") + '</ul>' : '') +
  '</div>';
}
function renderAdvisorRecommendation(rec) {
  const active = rec.id === advisorState.selectedRecommendationId ? " active" : "";
  return '<div class="advisor-rec' + active + '" data-advisor-rec-id="' + advisorHtml(rec.id) + '">' +
    '<button type="button" class="advisor-rec-main" data-advisor-open="' + advisorHtml(rec.id) + '">' +
      '<span class="ai-badge' + (rec.risk === "high" ? " high" : rec.risk === "low" ? " low" : "") + '">' + advisorHtml(rec.badge || rec.action) + '</span>' +
      '<span class="advisor-rec-text"><b>' + advisorHtml(rec.reason_short) + '</b><span>' + advisorHtml(rec.target_path) + '</span></span>' +
      '<span class="advisor-priority">' + advisorHtml(rec.priority) + '</span>' +
    '</button>' +
  '</div>';
}
function renderAdvisorRecommendations() {
  const rows = advisorState.payload && advisorState.payload.recommendations || [];
  if (!rows.length) return '<div class="advisor-empty">No active recommendations. Exclusions may be hiding previous findings.</div>';
  return '<div class="advisor-rec-list">' + rows.map(renderAdvisorRecommendation).join("") + '</div>';
}
function advisorEvidenceHtml(rec) {
  const rows = Array.isArray(rec.evidence) ? rec.evidence : [];
  if (!rows.length) return '<li>No extra evidence supplied.</li>';
  return rows.slice(0, 8).map(ev => '<li><span>' + advisorHtml(ev.label || ev.type || "evidence") + '</span><code>' + advisorHtml(ev.value) + '</code></li>').join("");
}
function renderAdvisorDetails() {
  const rec = getAdvisorRecommendation(advisorState.selectedRecommendationId);
  if (!rec) return '<div class="advisor-details-empty">Select a badge or recommendation to inspect why it was suggested.</div>';
  const safeDelete = isAdvisorSafeDeleteRecommendation(rec);
  return '<div class="advisor-details-card">' +
    '<div class="advisor-details-head"><span class="ai-badge' + (rec.risk === "high" ? " high" : rec.risk === "low" ? " low" : "") + '">' + advisorHtml(rec.badge || rec.action) + '</span>' +
      '<button type="button" class="advisor-close" data-advisor-close="1">×</button></div>' +
    '<h3>' + advisorHtml(rec.reason_short) + '</h3>' +
    '<p>' + advisorHtml(rec.reason_detail) + '</p>' +
    '<dl class="advisor-facts">' +
      '<div><dt>Action</dt><dd>' + advisorHtml(rec.action) + '</dd></div>' +
      '<div><dt>Risk</dt><dd>' + advisorHtml(rec.risk) + '</dd></div>' +
      '<div><dt>Confidence</dt><dd>' + Math.round((Number(rec.confidence) || 0) * 100) + '%</dd></div>' +
      '<div><dt>Size</dt><dd>' + advisorBytes(rec.bytes) + '</dd></div>' +
      '<div class="wide"><dt>Path</dt><dd><code>' + advisorHtml(rec.target_path) + '</code></dd></div>' +
      '<div class="wide"><dt>Next step</dt><dd>' + advisorHtml(rec.suggested_next_step) + '</dd></div>' +
    '</dl>' +
    '<h4>Evidence</h4><ul class="advisor-evidence">' + advisorEvidenceHtml(rec) + '</ul>' +
    '<div class="advisor-detail-actions">' +
      '<button type="button" class="primary" data-advisor-select-delete="' + advisorHtml(rec.id) + '"' + (safeDelete ? '' : ' disabled title="Only safe delete recommendations can be sent to the copy-only command builder"') + '>Select delete candidate</button>' +
      '<button type="button" data-advisor-exclude="recommendation" data-advisor-value="' + advisorHtml(rec.id) + '">Exclude recommendation</button>' +
      '<button type="button" data-advisor-exclude="path" data-advisor-value="' + advisorHtml(rec.target_path) + '">Exclude path</button>' +
      '<button type="button" data-advisor-exclude="action" data-advisor-value="' + advisorHtml(rec.action) + '">Exclude action</button>' +
    '</div>' +
  '</div>';
}
function renderAdvisorExclusions() {
  const items = advisorState.exclusions && advisorState.exclusions.items || [];
  if (!items.length) return '<div class="advisor-empty small">No exclusions saved for this host.</div>';
  return '<ul class="advisor-exclusions">' + items.map(item => {
    const value = item.id || item.path || item.pattern || item.action || item.category || "";
    return '<li><span>' + advisorHtml(item.type) + '</span><code>' + advisorHtml(value) + '</code></li>';
  }).join("") + '</ul>';
}
function renderAdvisorPanel() {
  if (typeof document === "undefined") return;
  const status = document.getElementById("advisorStatus");
  const summary = document.getElementById("advisorSummary");
  const list = document.getElementById("advisorRecommendations");
  const details = document.getElementById("advisorDetails");
  const exclusions = document.getElementById("advisorExclusions");
  const run = document.getElementById("advisorRun");
  const refresh = document.getElementById("advisorRefresh");
  if (status) {
    status.className = "advisor-status " + advisorStatusLabel();
    status.textContent = "AI Advisor: " + advisorStatusLabel();
    status.title = advisorState.error || advisorState.status.message || "";
  }
  if (run) {
    run.disabled = advisorState.running || !advisorState.status.enabled;
    run.textContent = advisorState.running ? "Running…" : "Run advisor";
  }
  if (refresh) refresh.disabled = advisorState.running;
  if (summary) summary.innerHTML = (advisorState.error ? '<div class="advisor-error">' + advisorHtml(advisorState.error) + '</div>' : '') + renderAdvisorSummary();
  if (list) list.innerHTML = renderAdvisorRecommendations();
  if (details) details.innerHTML = renderAdvisorDetails();
  if (exclusions) exclusions.innerHTML = renderAdvisorExclusions();
}
async function advisorRefreshStatus() {
  advisorState.status = typeof fetchAdvisorStatus === "function" ? await fetchAdvisorStatus() : advisorState.status;
  renderAdvisorPanel();
  return advisorState.status;
}
async function advisorRun(forceRefresh) {
  advisorState.running = true;
  advisorState.error = "";
  renderAdvisorPanel();
  try {
    advisorState.rawPayload = await requestAdvisorRecommendations({
      host_id: currentAdvisorHostId(),
      exclusions: advisorState.exclusions,
      max_items: 25,
      force_refresh: !!forceRefresh,
    });
    advisorApplyFilteredPayload();
    const rows = advisorState.payload.recommendations || [];
    advisorState.selectedRecommendationId = rows[0] ? rows[0].id : "";
  } catch (e) {
    advisorState.error = String(e && e.message || e);
  } finally {
    advisorState.running = false;
    renderAdvisorPanel();
  }
}
function advisorAddExclusion(type, value) {
  const item = { type };
  if (type === "recommendation") item.id = value;
  if (type === "path") item.path = value;
  if (type === "pattern") item.pattern = value;
  if (type === "action") item.action = value;
  if (type === "category") item.category = value;
  advisorState.exclusions = addAdvisorExclusionItem(currentAdvisorHostId(), advisorState.exclusions, item);
  advisorApplyFilteredPayload();
  renderAdvisorPanel();
}
function advisorClearExclusions() {
  advisorState.exclusions = saveAdvisorExclusions(currentAdvisorHostId(), defaultAdvisorExclusions(currentAdvisorHostId()));
  advisorApplyFilteredPayload();
  renderAdvisorPanel();
}
function advisorOpenDetails(id) {
  advisorState.selectedRecommendationId = id || "";
  renderAdvisorPanel();
  const panel = typeof document !== "undefined" && document.getElementById("advisorDetails");
  if (panel && panel.scrollIntoView) panel.scrollIntoView({ block: "nearest" });
}
function bindAdvisorUI() {
  if (typeof document === "undefined" || document._advisorUiBound) return;
  document._advisorUiBound = true;
  document.addEventListener("click", e => {
    const target = e.target && e.target.closest ? e.target.closest("[data-advisor-rec-id], [data-advisor-open], [data-advisor-select-delete], [data-advisor-exclude], [data-advisor-clear-exclusions], [data-advisor-close]") : null;
    if (!target) return;
    const badge = target.closest && target.closest(".ai-badge");
    if (badge) e.stopPropagation();
    if (target.dataset.advisorRecId) { advisorOpenDetails(target.dataset.advisorRecId); return; }
    if (target.dataset.advisorOpen) { advisorOpenDetails(target.dataset.advisorOpen); return; }
    if (target.dataset.advisorSelectDelete) { advisorSelectDeleteCandidate(target.dataset.advisorSelectDelete); return; }
    if (target.dataset.advisorExclude) { advisorAddExclusion(target.dataset.advisorExclude, target.dataset.advisorValue || ""); return; }
    if (target.dataset.advisorClearExclusions) { advisorClearExclusions(); return; }
    if (target.dataset.advisorClose) { advisorOpenDetails(""); }
  });
  const run = document.getElementById("advisorRun");
  if (run) run.onclick = () => advisorRun(false);
  const refresh = document.getElementById("advisorRefresh");
  if (refresh) refresh.onclick = () => advisorRefreshStatus();
  const clear = document.getElementById("advisorClearExclusions");
  if (clear) clear.onclick = advisorClearExclusions;
}
function onAdvisorHostChanged(host, data) {
  advisorState.host = host || null;
  advisorState.data = data || null;
  advisorState.exclusions = typeof loadAdvisorExclusions === "function" ? loadAdvisorExclusions(currentAdvisorHostId()) : { version: 1, host_id: currentAdvisorHostId(), items: [] };
  advisorState.rawPayload = null;
  advisorState.payload = null;
  advisorState.selectedRecommendationId = "";
  advisorState.error = "";
  if (typeof advisorSetRecommendations === "function") advisorSetRecommendations([]);
  renderAdvisorPanel();
  advisorRefreshStatus();
}
function initAdvisorUI() {
  bindAdvisorUI();
  renderAdvisorPanel();
  advisorRefreshStatus();
}

if (typeof globalThis !== "undefined") {
  Object.assign(globalThis, {
    advisorState,
    initAdvisorUI,
    bindAdvisorUI,
    onAdvisorHostChanged,
    renderAdvisorPanel,
    advisorRefreshStatus,
    advisorRun,
    advisorOpenDetails,
    advisorAddExclusion,
    advisorClearExclusions,
    advisorSelectDeleteCandidate,
    isAdvisorSafeDeleteRecommendation,
  });
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { isAdvisorSafeDeleteRecommendation };
}
