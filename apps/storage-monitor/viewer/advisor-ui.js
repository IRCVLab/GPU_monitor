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
  filters: { action: "", category: "", risk: "" },
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
function advisorVisibleRecommendations() {
  const rows = advisorState.payload && advisorState.payload.recommendations || [];
  const f = advisorState.filters || {};
  return rows.filter(rec => (!f.action || rec.action === f.action) && (!f.category || rec.category === f.category) && (!f.risk || rec.risk === f.risk));
}
function advisorUniqueValues(key) {
  const rows = advisorState.payload && advisorState.payload.recommendations || [];
  return Array.from(new Set(rows.map(rec => rec && rec[key]).filter(Boolean))).sort();
}
function advisorFilterSelectHtml(key, label) {
  const current = (advisorState.filters && advisorState.filters[key]) || "";
  const opts = ['<option value="">All ' + advisorHtml(label).toLowerCase() + '</option>'].concat(advisorUniqueValues(key).map(value =>
    '<option value="' + advisorHtml(value) + '"' + (value === current ? ' selected' : '') + '>' + advisorHtml(value) + '</option>'
  ));
  return '<label>' + advisorHtml(label) + '<select data-advisor-filter="' + advisorHtml(key) + '">' + opts.join("") + '</select></label>';
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
  return advisorState.status.cached ? "cached" : (advisorState.status.mode || "ready");
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
function renderAdvisorFilters() {
  const rows = advisorState.payload && advisorState.payload.recommendations || [];
  if (!rows.length) return "";
  return advisorFilterSelectHtml("action", "Action") + advisorFilterSelectHtml("category", "Category") + advisorFilterSelectHtml("risk", "Risk");
}
function renderAdvisorRecommendations() {
  const rows = advisorVisibleRecommendations();
  if (!((advisorState.payload && advisorState.payload.recommendations || []).length)) return '<div class="advisor-empty">No active recommendations. Exclusions may be hiding previous findings.</div>';
  if (!rows.length) return '<div class="advisor-empty">No recommendations match the current filters.</div>';
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
  const filters = document.getElementById("advisorFilters");
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
  const payload = advisorState.payload;
  const summary = payload && payload.summary ? payload.summary : null;
  if (summary) {
    summaryEl.innerHTML = '<div class="advisor-health ' + advisorHealthClass(summary.health) + '">' + htmlEscape(summary.health || "ok") + '</div>' +
      '<div><b>' + htmlEscape(summary.headline || "Advisor summary") + '</b><br>' +
      (summary.top_drivers || []).map(d => '<span class="advisor-driver">' + htmlEscape(d) + '</span>').join("") + '</div>';
  } else {
    summaryEl.innerHTML = '<div class="advisor-empty">Run the local advisor to see evidence-backed cleanup suggestions. Existing dashboard rendering is independent of AI.</div>';
  }
  if (refresh) refresh.disabled = advisorState.running;
  if (summary) summary.innerHTML = (advisorState.error ? '<div class="advisor-error">' + advisorHtml(advisorState.error) + '</div>' : '') + renderAdvisorSummary();
  if (filters) filters.innerHTML = renderAdvisorFilters();
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

function advisorRecommendationHtml(rec) {
  const canDelete = typeof isDeleteSelectableRecommendation === "function" && isDeleteSelectableRecommendation(rec);
  return '<article class="advisor-rec" data-advisor-rec="' + htmlEscape(rec.id) + '">' +
    '<div class="advisor-rec-head"><button type="button" class="linklike ai-badge" data-advisor-rec="' + htmlEscape(rec.id) + '">' + htmlEscape(rec.badge || rec.category) + '</button>' +
    '<span class="advisor-pill">' + htmlEscape(rec.action) + '</span><span class="advisor-pill risk-' + htmlEscape(rec.risk) + '">' + htmlEscape(rec.risk) + '</span></div>' +
    '<div class="advisor-target"><bdi>' + htmlEscape(rec.target_path) + '</bdi></div>' +
    '<div class="advisor-reason">' + htmlEscape(rec.reason_short || '') + '</div>' +
    '<div class="advisor-actions"><button type="button" data-advisor-detail="' + htmlEscape(rec.id) + '">Details</button>' +
    (canDelete ? '<button type="button" data-advisor-select="' + htmlEscape(rec.id) + '">Select delete candidate</button>' : '') +
    '<button type="button" data-advisor-exclude="recommendation" data-advisor-rec="' + htmlEscape(rec.id) + '">Exclude</button></div>' +
    '</article>';
}

function showAdvisorDetail(id) {
  if (typeof document === "undefined") return;
  const rec = ((advisorState && advisorState.recommendations) || []).find(r => r.id === id) ||
    (((advisorState && advisorState.payload && advisorState.payload.recommendations) || []).find(r => r.id === id));
  const drawer = document.getElementById("advisorDetail");
  if (!drawer || !rec) return;
  drawer.classList.add("show");
  drawer.setAttribute("aria-hidden", "false");
  drawer.innerHTML = '<div class="advisor-detail-head"><h3>' + htmlEscape(rec.badge || rec.category) + '</h3><button type="button" id="advisorDetailClose">×</button></div>' +
    '<p><b>Action:</b> ' + htmlEscape(rec.action) + ' · <b>Risk:</b> ' + htmlEscape(rec.risk) + ' · <b>Confidence:</b> ' + Math.round(Number(rec.confidence || 0) * 100) + '%</p>' +
    '<p><b>Target:</b> <bdi>' + htmlEscape(rec.target_path) + '</bdi></p>' +
    (rec.related_paths && rec.related_paths.length ? '<p><b>Related:</b> ' + rec.related_paths.map(p => '<bdi>' + htmlEscape(p) + '</bdi>').join(', ') + '</p>' : '') +
    '<p>' + htmlEscape(rec.reason_detail || rec.reason_short || '') + '</p>' +
    '<ul class="advisor-evidence">' + (rec.evidence || []).map(ev => '<li><b>' + htmlEscape(ev.label || ev.type || 'evidence') + ':</b> ' + htmlEscape(String(ev.value)) + '</li>').join('') + '</ul>' +
    '<div class="advisor-actions">' + (isDeleteSelectableRecommendation(rec) ? '<button type="button" data-advisor-select="' + htmlEscape(rec.id) + '">Select delete candidate</button>' : '') +
    '<button type="button" data-advisor-exclude="path" data-advisor-rec="' + htmlEscape(rec.id) + '">Exclude path</button>' +
    '<button type="button" data-advisor-exclude="action" data-advisor-rec="' + htmlEscape(rec.id) + '">Exclude action</button></div>';
  const close = document.getElementById("advisorDetailClose");
  if (close) close.onclick = () => hideAdvisorDetail();
}

function hideAdvisorDetail() {
  const drawer = typeof document !== "undefined" ? document.getElementById("advisorDetail") : null;
  if (!drawer) return;
  drawer.classList.remove("show");
  drawer.setAttribute("aria-hidden", "true");
}

function selectAdvisorDeleteCandidate(id) {
  const rec = ((advisorState && advisorState.recommendations) || []).find(r => r.id === id);
  if (!rec || !isDeleteSelectableRecommendation(rec) || typeof setCleanupSelectedItem !== "function") return false;
  const selected = setCleanupSelectedItem({ path: rec.target_path, bytes: rec.bytes || 0, owner: rec.owner || "", source: "ai-advisor" }, true);
  if (typeof renderCleanupPanel === "function") renderCleanupPanel();
  return selected;
}

function bindAdvisorUi() {
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
  document.addEventListener("change", e => {
    const sel = e.target && e.target.closest ? e.target.closest("[data-advisor-filter]") : null;
    if (!sel) return;
    advisorState.filters[sel.dataset.advisorFilter] = sel.value || "";
    renderAdvisorPanel();
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
  advisorState.filters = { action: "", category: "", risk: "" };
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
    renderAdvisorFilters,
    advisorVisibleRecommendations,
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
  module.exports = { advisorRecommendationHtml, advisorHealthClass };
}
