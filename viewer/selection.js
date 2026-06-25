"use strict";
/* =========================================================================
   Cleanup-assist selection + delete command generation.
   The app only generates copyable commands. It never deletes files.
   ========================================================================= */
const selectedPaths = new Map();

function shellQuote(path) {
  return "'" + String(path).replace(/'/g, "'\"'\"'") + "'";
}

function isSafeAbsolutePath(path) {
  return typeof path === "string" && path.startsWith("/") && path.length > 1 && !/[\r\n\0]/.test(path);
}

function buildDeleteCommands(items) {
  const warnings = [];
  const valid = [];
  const seen = new Set();
  for (const item of items || []) {
    const p = String(item && item.path || "");
    if (!isSafeAbsolutePath(p)) { warnings.push("Skipped unsafe/non-absolute path: " + p); continue; }
    if (seen.has(p)) continue;
    seen.add(p);
    valid.push({ ...item, path: p });
  }
  valid.sort((a, b) => a.path.length - b.path.length || a.path.localeCompare(b.path));
  const kept = [];
  for (const item of valid) {
    const parent = kept.find(k => item.path.startsWith(k.path.endsWith("/") ? k.path : k.path + "/"));
    if (parent) {
      warnings.push("Suppressed descendant already covered by parent command: " + item.path);
    } else {
      kept.push(item);
    }
  }
  return {
    commands: kept.map(item => "sudo rm -rf -- " + shellQuote(item.path)),
    warnings,
    items: kept,
  };
}

function selectedBytes(items) {
  return items.reduce((sum, item) => sum + (Number(item.bytes) || 0), 0);
}

function selectionCellHtml(item, source) {
  const path = item && item.path ? String(item.path) : "";
  const checked = selectedPaths.has(path) ? " checked" : "";
  const title = path ? "Select for manual cleanup command" : "No path";
  return '<input class="select-check" type="checkbox" aria-label="Select cleanup candidate" title="' + escapeHtml(title) + '" data-select-path="' +
    escapeHtml(path) + '" data-select-bytes="' + escapeHtml(item && item.bytes != null ? item.bytes : "") + '" data-select-source="' +
    escapeHtml(source || "") + '"' + checked + '>';
}

function refreshSelectionChecks() {
  if (typeof document === "undefined") return;
  document.querySelectorAll(".select-check[data-select-path]").forEach(cb => {
    cb.checked = selectedPaths.has(cb.dataset.selectPath || "");
  });
}

function renderDeletePanel() {
  if (typeof document === "undefined") return;
  const panel = document.getElementById("deletePanel");
  if (!panel) return;
  const items = [...selectedPaths.values()];
  panel.hidden = items.length === 0;
  if (!items.length) return;
  const built = buildDeleteCommands(items);
  const commandsText = built.commands.join("\n");
  const countEl = document.getElementById("deleteCount");
  const ta = document.getElementById("deleteCommands");
  const warnings = document.getElementById("deleteWarnings");
  if (countEl) countEl.textContent = items.length + " selected · " + humanBytes(selectedBytes(items)) + " marked";
  if (ta) ta.value = commandsText;
  if (warnings) warnings.textContent = built.warnings.join("\n");
}

function setSelectedPath(path, selected, meta) {
  if (selected) selectedPaths.set(path, { path, ...meta });
  else selectedPaths.delete(path);
  renderDeletePanel();
  refreshSelectionChecks();
}

function clearSelectedPaths() {
  selectedPaths.clear();
  renderDeletePanel();
  refreshSelectionChecks();
}

async function copyDeleteCommands() {
  const ta = document.getElementById("deleteCommands");
  const btn = document.getElementById("copyDeleteCommands");
  if (!ta || !btn) return;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(ta.value);
    else { ta.focus(); ta.select(); document.execCommand("copy"); }
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = "Copy commands"; }, 1200);
  } catch (e) {
    ta.focus(); ta.select();
    btn.textContent = "Select text";
  }
}

function initSelectionPanel() {
  if (typeof document === "undefined") return;
  document.addEventListener("change", (e) => {
    const cb = e.target.closest(".select-check[data-select-path]");
    if (!cb) return;
    const path = cb.dataset.selectPath || "";
    setSelectedPath(path, cb.checked, {
      bytes: Number(cb.dataset.selectBytes || 0) || 0,
      source: cb.dataset.selectSource || "",
    });
  });
  const copy = document.getElementById("copyDeleteCommands");
  if (copy) copy.onclick = copyDeleteCommands;
  const clear = document.getElementById("clearDeleteSelection");
  if (clear) clear.onclick = clearSelectedPaths;
  renderDeletePanel();
}

if (typeof module !== "undefined" && module.exports) module.exports = { shellQuote, buildDeleteCommands, isSafeAbsolutePath };
