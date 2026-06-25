"use strict";

/* Optional AI Advisor UI: status, recommendation cards, details, exclusions, safe cleanup handoff. */
function advisorEscape(value) {
  if (typeof escapeHtml === "function") return escapeHtml(value);
  return String(value == null ? "" : value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function advisorBytes(value) {
  return typeof humanBytes === "function" ? humanBytes(value) : String(Number(value) || 0) + " B";
}
function advisorPathDepth(path) {
  return String(path || "").split("/").filter(Boolean).length;
}
function isAdvisorSafeDeleteRecommendation(rec) {
  if (!rec) return false;
  if (typeof isDeleteSelectableRecommendation === "function" && !isDeleteSelectableRecommendation(rec)) return false;
  if (rec.action !== "delete" || rec.suggested_next_step !== "review-delete-command") return false;
  if (typeof rec.target_path !== "string" || !rec.target_path.startsWith("/") || rec.target_path.includes("\0")) return false;
  if (typeof isTopLevelCleanupPath === "function" && isTopLevelCleanupPath(rec.target_path)) return false;
  if (typeof isTopLevelCleanupPath !== "function" && advisorPathDepth(rec.target_path) <= 1) return false;
  return true;
}
function advisorCurrentHostId() {
  const sel = typeof document !== "undefined" ? document.getElementById("hostSel") : null;
  if (sel && sel.value) return sel.value;
  return (typeof DATA !== "undefined" && DATA && DATA.hostname) || "hinton";
}
function advisorRows() {
  return (typeof advisorState !== "undefined" && Array.isArray(advisorState.recommendations)) ? advisorState.recommendations : [];
}
function advisorFindRecommendation(id) {
  return advisorRows().find(rec => rec && rec.id === id) || null;
}
function advisorHealthClass(health) {
  const value = String(health || (advisorState && advisorState.payload && advisorState.payload.summary && advisorState.payload.summary.health) || "ok");
  return value === "critical" ? "crit" : value === "warning" ? "warn" : "ok";
}
function advisorStatusLabel() {
  if (advisorState && advisorState.running) return "running";
  if (advisorState && advisorState.error) return "failed";
  if (!advisorState || !advisorState.status || !advisorState.status.enabled) return "disabled";
  return advisorState.status.cached ? "cached" : "ready";
}
function renderAdvisorSummaryHtml() {
  const payload = advisorState && advisorState.payload;
  if (!payload || !payload.summary) {
    return '<div class="advisor-empty">Run the local advisor to see evidence-backed cleanup suggestions. Existing dashboard rendering is independent of AI.</div>';
  }
  const summary = payload.summary;
  const drivers = Array.isArray(summary.top_drivers) ? summary.top_drivers : [];
  return '<div class="advisor-health ' + advisorHealthClass(summary.health) + '">' + advisorEscape(summary.health || "ok") + '</div>' +
    '<div><b>' + advisorEscape(summary.headline || "Advisor summary") + '</b><br>' +
    drivers.slice(0, 4).map(d => '<span class="advisor-driver">' + advisorEscape(d) + '</span>').join("") + '</div>' +
    (advisorState && advisorState.error ? '<div class="advisor-error">' + advisorEscape(advisorState.error) + '</div>' : '');
}
function advisorRecommendationHtml(rec) {
  const canDelete = isAdvisorSafeDeleteRecommendation(rec);
  return '<article class="advisor-rec" data-advisor-rec="' + advisorEscape(rec.id) + '">' +
    '<div class="advisor-rec-head"><button type="button" class="linklike ai-badge" data-advisor-detail="' + advisorEscape(rec.id) + '">' + advisorEscape(rec.badge || rec.category || rec.action || "AI") + '</button>' +
    '<span class="advisor-pill">' + advisorEscape(rec.action || "review") + '</span><span class="advisor-pill risk-' + advisorEscape(rec.risk || "medium") + '">' + advisorEscape(rec.risk || "medium") + '</span></div>' +
    '<div class="advisor-target"><bdi>' + advisorEscape(rec.target_path || "") + '</bdi></div>' +
    '<div class="advisor-reason">' + advisorEscape(rec.reason_short || "") + '</div>' +
    '<div class="advisor-actions"><button type="button" data-advisor-detail="' + advisorEscape(rec.id) + '">Details</button>' +
    (canDelete ? '<button type="button" data-advisor-select="' + advisorEscape(rec.id) + '">Select delete candidate</button>' : '') +
    '<button type="button" data-advisor-exclude="recommendation" data-advisor-rec="' + advisorEscape(rec.id) + '">Exclude</button></div>' +
    '</article>';
}
function renderAdvisorListHtml() {
  const rows = advisorRows();
  if (!rows.length) return '<div class="advisor-empty">No active recommendations. AI may be disabled, not yet run, or exclusions may be hiding findings.</div>';
  return rows.map(advisorRecommendationHtml).join("");
}
function renderAdvisorExclusionsHtml() {
  const rows = (advisorState && Array.isArray(advisorState.exclusions)) ? advisorState.exclusions : [];
  if (!rows.length) return '<span class="muted">No exclusions saved for this host.</span>';
  return rows.map(item => {
    const value = item.id || item.path || item.pattern || item.action || item.category || "";
    return '<span class="advisor-exclusion">' + advisorEscape(item.type || "exclude") + ': <code>' + advisorEscape(value) + '</code></span>';
  }).join("");
}
function renderAdvisorPanel() {
  if (typeof document === "undefined") return;
  const status = document.getElementById("advisorStatus");
  const run = document.getElementById("advisorRun");
  const summary = document.getElementById("advisorSummary");
  const list = document.getElementById("advisorList");
  const exclusions = document.getElementById("advisorExclusions");
  if (status) {
    const label = advisorStatusLabel();
    status.className = "advisor-status " + label;
    status.textContent = "AI Advisor: " + label;
    status.title = (advisorState && (advisorState.error || (advisorState.status && advisorState.status.message))) || "";
  }
  if (run) run.disabled = !!(advisorState && advisorState.running) || !(advisorState && advisorState.status && advisorState.status.enabled);
  if (summary) summary.innerHTML = renderAdvisorSummaryHtml();
  if (list) list.innerHTML = renderAdvisorListHtml();
  if (exclusions) exclusions.innerHTML = renderAdvisorExclusionsHtml();
}
async function advisorRefreshStatus() {
  if (typeof fetchAdvisorStatus === "function") await fetchAdvisorStatus();
  renderAdvisorPanel();
}
async function advisorRunNow(forceRefresh) {
  if (typeof runAdvisor === "function") await runAdvisor({ force_refresh: !!forceRefresh });
  renderAdvisorPanel();
}
function showAdvisorDetail(id) {
  if (typeof document === "undefined") return;
  const rec = advisorFindRecommendation(id);
  const drawer = document.getElementById("advisorDetail");
  if (!drawer || !rec) return;
  const evidence = Array.isArray(rec.evidence) ? rec.evidence : [];
  drawer.classList.add("show");
  drawer.setAttribute("aria-hidden", "false");
  drawer.innerHTML = '<div class="advisor-detail-head"><h3>' + advisorEscape(rec.badge || rec.category || "AI recommendation") + '</h3><button type="button" id="advisorDetailClose">×</button></div>' +
    '<p><b>Action:</b> ' + advisorEscape(rec.action) + ' · <b>Risk:</b> ' + advisorEscape(rec.risk) + ' · <b>Confidence:</b> ' + Math.round(Number(rec.confidence || 0) * 100) + '%</p>' +
    '<p><b>Size:</b> ' + advisorBytes(rec.bytes) + '</p>' +
    '<p><b>Target:</b> <bdi>' + advisorEscape(rec.target_path) + '</bdi></p>' +
    (rec.related_paths && rec.related_paths.length ? '<p><b>Related:</b> ' + rec.related_paths.map(p => '<bdi>' + advisorEscape(p) + '</bdi>').join(', ') + '</p>' : '') +
    '<p>' + advisorEscape(rec.reason_detail || rec.reason_short || '') + '</p>' +
    '<ul class="advisor-evidence">' + evidence.slice(0, 8).map(ev => '<li><b>' + advisorEscape(ev.label || ev.type || 'evidence') + ':</b> ' + advisorEscape(String(ev.value)) + '</li>').join('') + '</ul>' +
    '<div class="advisor-actions">' + (isAdvisorSafeDeleteRecommendation(rec) ? '<button type="button" data-advisor-select="' + advisorEscape(rec.id) + '">Select delete candidate</button>' : '') +
    '<button type="button" data-advisor-exclude="recommendation" data-advisor-rec="' + advisorEscape(rec.id) + '">Exclude recommendation</button>' +
    '<button type="button" data-advisor-exclude="path" data-advisor-rec="' + advisorEscape(rec.id) + '">Exclude path</button>' +
    '<button type="button" data-advisor-exclude="action" data-advisor-rec="' + advisorEscape(rec.id) + '">Exclude action</button></div>';
  const close = document.getElementById("advisorDetailClose");
  if (close) close.onclick = hideAdvisorDetail;
}
function hideAdvisorDetail() {
  const drawer = typeof document !== "undefined" ? document.getElementById("advisorDetail") : null;
  if (!drawer) return;
  drawer.classList.remove("show");
  drawer.setAttribute("aria-hidden", "true");
}
function selectAdvisorDeleteCandidate(id) {
  const rec = advisorFindRecommendation(id);
  if (!isAdvisorSafeDeleteRecommendation(rec) || typeof setCleanupSelectedItem !== "function") return false;
  const selected = setCleanupSelectedItem({ path: rec.target_path, bytes: rec.bytes || 0, owner: rec.owner || "", source: "ai-advisor" }, true);
  if (typeof renderCleanupPanel === "function") renderCleanupPanel();
  return selected;
}
function advisorAddExclusion(type, id) {
  const rec = advisorFindRecommendation(id);
  const exclusion = type === "recommendation" ? { type, id } :
    type === "path" && rec ? { type, path: rec.target_path } :
    type === "action" && rec ? { type, action: rec.action } : { type, id };
  if (typeof addAdvisorExclusion === "function") addAdvisorExclusion(advisorCurrentHostId(), exclusion);
  renderAdvisorPanel();
}
function advisorClearExclusions() {
  if (typeof clearAdvisorExclusions === "function") clearAdvisorExclusions(advisorCurrentHostId());
  renderAdvisorPanel();
}
function bindAdvisorUi() {
  if (typeof document === "undefined" || document._advisorUiBound) return;
  document._advisorUiBound = true;
  document.addEventListener("click", e => {
    const target = e.target && e.target.closest ? e.target.closest("[data-advisor-detail], [data-advisor-select], [data-advisor-exclude], [data-advisor-clear-exclusions]") : null;
    if (!target) return;
    if (target.dataset.advisorDetail) { e.stopPropagation(); showAdvisorDetail(target.dataset.advisorDetail); return; }
    if (target.dataset.advisorSelect) { e.stopPropagation(); selectAdvisorDeleteCandidate(target.dataset.advisorSelect); return; }
    if (target.dataset.advisorExclude) { e.stopPropagation(); advisorAddExclusion(target.dataset.advisorExclude, target.dataset.advisorRec || target.dataset.advisorDetail || ""); return; }
    if (target.dataset.advisorClearExclusions) { e.stopPropagation(); advisorClearExclusions(); }
  });
  const run = document.getElementById("advisorRun");
  if (run) run.onclick = () => advisorRunNow(false);
}
function onAdvisorHostChanged(host, data) {
  if (typeof advisorState !== "undefined") {
    advisorState.payload = null;
    advisorState.recommendations = [];
    advisorState.error = null;
    advisorState.host = host || null;
    advisorState.data = data || null;
    if (typeof loadAdvisorExclusions === "function") loadAdvisorExclusions((host && host.id) || advisorCurrentHostId());
  }
  if (typeof advisorSetRecommendations === "function") advisorSetRecommendations([]);
  renderAdvisorPanel();
  advisorRefreshStatus();
}
function initAdvisorUI() {
  bindAdvisorUi();
  renderAdvisorPanel();
  advisorRefreshStatus();
}

if (typeof globalThis !== "undefined") {
  Object.assign(globalThis, {
    initAdvisorUI,
    bindAdvisorUi,
    bindAdvisorUI: bindAdvisorUi,
    onAdvisorHostChanged,
    renderAdvisorPanel,
    advisorRefreshStatus,
    advisorRun: advisorRunNow,
    showAdvisorDetail,
    hideAdvisorDetail,
    advisorAddExclusion,
    advisorClearExclusions,
    selectAdvisorDeleteCandidate,
    advisorSelectDeleteCandidate: selectAdvisorDeleteCandidate,
    isAdvisorSafeDeleteRecommendation,
  });
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { advisorRecommendationHtml, advisorHealthClass, isAdvisorSafeDeleteRecommendation };
}
