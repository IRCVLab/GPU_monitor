"use strict";

function advisorHealthClass(health) {
  return health === "critical" ? "crit" : health === "warning" ? "warn" : "ok";
}

function renderAdvisorPanel() {
  if (typeof document === "undefined") return;
  const statusEl = document.getElementById("advisorStatus");
  const summaryEl = document.getElementById("advisorSummary");
  const listEl = document.getElementById("advisorList");
  const exclEl = document.getElementById("advisorExclusions");
  const runBtn = document.getElementById("advisorRun");
  if (!statusEl || !summaryEl || !listEl) return;
  const status = advisorState.status || {};
  const provider = status.provider ? " · " + status.provider : "";
  const model = status.model ? " · " + status.model : "";
  statusEl.className = "advisor-status " + (status.enabled ? "enabled" : "disabled");
  statusEl.textContent = advisorState.running ? "AI Advisor running…" : ((status.enabled ? "Enabled" : "Disabled") + provider + model + " — " + (status.message || ""));
  if (runBtn) {
    runBtn.disabled = advisorState.running || !status.enabled;
    runBtn.textContent = advisorState.running ? "Running…" : "Run advisor";
    runBtn.title = status.enabled ? "Generate local cleanup advice" : (status.message || "AI Advisor disabled");
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
  if (advisorState.error) {
    summaryEl.innerHTML += '<div class="advisor-error">' + htmlEscape(advisorState.error) + '</div>';
  }
  const recs = advisorState.recommendations || [];
  if (!recs.length) {
    listEl.innerHTML = '<div class="advisor-empty">No active recommendations' + (advisorState.exclusions.length ? ' after exclusions.' : '.') + '</div>';
  } else {
    listEl.innerHTML = recs.map(rec => advisorRecommendationHtml(rec)).join("");
  }
  if (exclEl) {
    exclEl.innerHTML = advisorState.exclusions.length
      ? advisorState.exclusions.map(ex => '<span class="advisor-exclusion">' + htmlEscape(ex.type + ':' + (ex.id || ex.path || ex.pattern || ex.action || ex.category || '')) + '</span>').join("") + '<button type="button" id="advisorClearExclusions">Clear exclusions</button>'
      : '<span class="muted">No exclusions yet.</span>';
    const clear = document.getElementById("advisorClearExclusions");
    if (clear) clear.onclick = () => clearAdvisorExclusions(currentAdvisorHostId());
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
  const run = document.getElementById("advisorRun");
  if (run) run.onclick = () => runAdvisor({ force_refresh: true });
  document.addEventListener("click", (e) => {
    const detail = e.target && e.target.closest ? e.target.closest("[data-advisor-detail]") : null;
    if (detail) { showAdvisorDetail(detail.dataset.advisorDetail); return; }
    const select = e.target && e.target.closest ? e.target.closest("[data-advisor-select]") : null;
    if (select) { selectAdvisorDeleteCandidate(select.dataset.advisorSelect); return; }
    const exclude = e.target && e.target.closest ? e.target.closest("[data-advisor-exclude]") : null;
    if (exclude) {
      const rec = (advisorState.recommendations || []).find(r => r.id === exclude.dataset.advisorRec);
      if (!rec) return;
      const type = exclude.dataset.advisorExclude;
      const item = type === "path" ? { type, path: rec.target_path } : type === "action" ? { type, action: rec.action } : { type: "recommendation", id: rec.id };
      addAdvisorExclusion(currentAdvisorHostId(), item);
      return;
    }
  });
}

if (typeof globalThis !== "undefined") {
  Object.assign(globalThis, { renderAdvisorPanel, showAdvisorDetail, hideAdvisorDetail, bindAdvisorUi, selectAdvisorDeleteCandidate });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { advisorRecommendationHtml, advisorHealthClass };
}
