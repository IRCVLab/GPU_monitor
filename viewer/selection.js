"use strict";

/* Selection/delete-command state + helpers.
   Copy-only: this module generates commands but never executes deletion. */
var cleanupSelected = (typeof globalThis !== "undefined" && globalThis.cleanupSelected) ||
  (typeof Map !== "undefined" ? new Map() : null);

function htmlEscape(value) {
  if (typeof escapeHtml === "function") return escapeHtml(value);
  return String(value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function shellQuote(path) {
  return "'" + String(path).replace(/'/g, "'\"'\"'") + "'";
}

function shellQuotePath(path) {
  return shellQuote(path);
}

function isAbsoluteCleanupPath(path) {
  return typeof path === "string" && path.startsWith("/") && path !== "/" && !path.includes("\0");
}

function normalizeCleanupPath(path) {
  return String(path || "").replace(/\/+$/g, "") || "/";
}

function cleanupKey(path) {
  return normalizeCleanupPath(path);
}

function cleanupBytesLabel(bytes) {
  return typeof humanBytes === "function" ? humanBytes(bytes) : String(bytes || 0) + " B";
}

function dedupeDescendants(items) {
  const sorted = (items || [])
    .filter(item => item && isAbsoluteCleanupPath(item.path))
    .map(item => ({ ...item, path: normalizeCleanupPath(item.path) }))
    .sort((a, b) => a.path.length - b.path.length || a.path.localeCompare(b.path));
  const kept = [];
  const warnings = [];
  for (const item of sorted) {
    const parent = kept.find(k => item.path !== k.path && item.path.startsWith(k.path.endsWith("/") ? k.path : k.path + "/"));
    if (parent) {
      warnings.push(`${item.path} skipped because parent ${parent.path} is already selected`);
      continue;
    }
    if (!kept.some(k => k.path === item.path)) kept.push(item);
  }
  return { kept, warnings };
}

function buildDeleteCommands(items) {
  const warnings = [];
  const valid = [];
  for (const item of items || []) {
    const path = item && item.path;
    if (!isAbsoluteCleanupPath(path)) {
      if (path) warnings.push(`${path} skipped because cleanup commands require an absolute path`);
      continue;
    }
    valid.push(item);
  }
  const deduped = dedupeDescendants(valid);
  warnings.push(...deduped.warnings);
  const result = {
    commands: deduped.kept.map(item => `sudo rm -rf -- ${shellQuote(item.path)}`),
    warnings,
    items: deduped.kept,
    toString() { return this.commands.join("\n"); },
    split(separator) { return this.toString().split(separator); },
  };
  return result;
}

function cleanupCheckboxHtml(file, source) {
  const path = normalizeCleanupPath(file && file.path);
  if (!isAbsoluteCleanupPath(path)) {
    return '<span class="hint" title="Cleanup commands require an absolute path">—</span>';
  }
  const bytes = Number(file && file.bytes) || 0;
  const owner = String((file && file.owner) || "");
  const checked = cleanupSelected && cleanupSelected.has(cleanupKey(path)) ? " checked" : "";
  const label = "Select " + path + " (" + cleanupBytesLabel(bytes) + ") for delete command";
  return '<input type="checkbox" class="cleanup-check" data-path="' + htmlEscape(path) +
    '" data-bytes="' + htmlEscape(bytes) +
    '" data-owner="' + htmlEscape(owner) +
    '" data-source="' + htmlEscape(source || "") +
    '" aria-label="' + htmlEscape(label) + '"' + checked + '>';
}

function cleanupItemFromCheckbox(input) {
  const ds = input.dataset || {};
  return {
    path: normalizeCleanupPath(ds.path),
    bytes: Number(ds.bytes) || 0,
    owner: ds.owner || "",
    source: ds.source || "",
  };
}

function cleanupItems() {
  return cleanupSelected ? Array.from(cleanupSelected.values()) : [];
}

function isCleanupSelectedPath(path) {
  return !!(cleanupSelected && cleanupSelected.has(cleanupKey(path)));
}

function setCleanupSelectedItem(item, selected) {
  if (!cleanupSelected || !item || !isAbsoluteCleanupPath(item.path)) return false;
  const normalized = { ...item, path: normalizeCleanupPath(item.path) };
  if (selected) cleanupSelected.set(cleanupKey(normalized.path), normalized);
  else cleanupSelected.delete(cleanupKey(normalized.path));
  return isCleanupSelectedPath(normalized.path);
}

function toggleCleanupSelectedItem(item) {
  if (!cleanupSelected || !item || !isAbsoluteCleanupPath(item.path)) return false;
  return setCleanupSelectedItem(item, !isCleanupSelectedPath(item.path));
}

function ensureCleanupWarnings(panel) {
  if (typeof document === "undefined") return null;
  let el = document.getElementById("cleanupWarnings");
  if (!el && panel) {
    el = document.createElement("div");
    el.id = "cleanupWarnings";
    el.className = "delete-warnings";
    panel.appendChild(el);
  }
  return el;
}

function syncCleanupChecks() {
  if (typeof document === "undefined" || !cleanupSelected) return;
  document.querySelectorAll(".cleanup-check").forEach(input => {
    const path = cleanupKey(input.dataset && input.dataset.path);
    input.checked = cleanupSelected.has(path);
  });
}

function renderCleanupPanel() {
  const selected = cleanupItems();
  const result = buildDeleteCommands(selected);
  if (typeof document === "undefined") return result;

  const panel = document.getElementById("cleanupPanel");
  if (!panel) return result;
  const summary = document.getElementById("cleanupSummary");
  const commands = document.getElementById("cleanupCommands");
  const copy = document.getElementById("cleanupCopy");
  const clear = document.getElementById("cleanupClear");
  const warnings = ensureCleanupWarnings(panel);

  const hasSelection = selected.length > 0;
  panel.classList.toggle("show", hasSelection);
  panel.setAttribute("aria-hidden", hasSelection ? "false" : "true");

  const totalBytes = selected.reduce((sum, item) => sum + (Number(item.bytes) || 0), 0);
  if (summary) {
    summary.textContent = hasSelection
      ? selected.length + " selected · " + cleanupBytesLabel(totalBytes)
      : "0 selected";
  }
  if (commands) commands.value = result.toString();
  if (copy) copy.disabled = result.commands.length === 0;
  if (clear) clear.disabled = !hasSelection;
  if (warnings) {
    warnings.innerHTML = result.warnings.length
      ? result.warnings.map(w => "⚠ " + htmlEscape(w)).join("<br>")
      : "";
  }
  syncCleanupChecks();
  if (typeof document !== "undefined" && document.dispatchEvent && typeof CustomEvent !== "undefined") {
    document.dispatchEvent(new CustomEvent("cleanup-selection-rendered", { detail: { items: selected, result } }));
  }
  return result;
}

async function writeClipboardText(text) {
  if (!text) return false;
  if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  if (typeof document !== "undefined" && document.execCommand) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
  return false;
}

function setButtonFeedback(btn, label, className) {
  if (!btn) return;
  const old = btn.textContent;
  btn.textContent = label;
  if (className) btn.classList.add(className);
  setTimeout(() => {
    btn.textContent = old;
    if (className) btn.classList.remove(className);
  }, 1200);
}

function bindCleanupSelection() {
  if (typeof document === "undefined" || !cleanupSelected) return;
  document.addEventListener("change", (e) => {
    const input = e.target && e.target.closest ? e.target.closest(".cleanup-check") : null;
    if (!input) return;
    const item = cleanupItemFromCheckbox(input);
    if (!isAbsoluteCleanupPath(item.path)) return;
    setCleanupSelectedItem(item, input.checked);
    renderCleanupPanel();
  });

  const clear = document.getElementById("cleanupClear");
  if (clear) clear.onclick = () => {
    cleanupSelected.clear();
    renderCleanupPanel();
  };

  const copy = document.getElementById("cleanupCopy");
  if (copy) copy.onclick = async () => {
    const result = renderCleanupPanel();
    const text = result.toString();
    try {
      const ok = await writeClipboardText(text);
      setButtonFeedback(copy, ok ? "Copied" : "Copy failed", ok ? "done" : "");
    } catch (_) {
      setButtonFeedback(copy, "Copy failed", "");
    }
  };

  renderCleanupPanel();
}

if (typeof globalThis !== "undefined") {
  globalThis.cleanupSelected = cleanupSelected;
  globalThis.shellQuote = shellQuote;
  globalThis.shellQuotePath = shellQuotePath;
  globalThis.buildDeleteCommands = buildDeleteCommands;
  globalThis.buildDeleteCommandsSafe = buildDeleteCommands;
  globalThis.cleanupCheckboxHtml = cleanupCheckboxHtml;
  globalThis.renderCleanupPanel = renderCleanupPanel;
  globalThis.bindCleanupSelection = bindCleanupSelection;
  globalThis.isCleanupSelectedPath = isCleanupSelectedPath;
  globalThis.setCleanupSelectedItem = setCleanupSelectedItem;
  globalThis.toggleCleanupSelectedItem = toggleCleanupSelectedItem;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    shellQuote,
    shellQuotePath,
    buildDeleteCommands,
    isAbsoluteCleanupPath,
    dedupeDescendants,
    cleanupCheckboxHtml,
    renderCleanupPanel,
    bindCleanupSelection,
    isCleanupSelectedPath,
    setCleanupSelectedItem,
    toggleCleanupSelectedItem,
  };
}
