"use strict";

/* Copy-only storage cleanup workflow.
   The browser may validate snapshot selections and copy fixed command templates,
   but it never executes filesystem commands. */

var cleanupSelectionState = (typeof globalThis !== "undefined" && globalThis.cleanupSelectionState) || {
  serverId: null,
  items: [],
};
if (!Array.isArray(cleanupSelectionState.items)) {
  cleanupSelectionState.items = cleanupSelectionState.item ? [cleanupSelectionState.item] : [];
}

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

function cleanupBytesLabel(bytes) {
  return typeof humanBytes === "function" ? humanBytes(bytes) : String(bytes || 0) + " B";
}

function cleanupBoundedReason(code, message) {
  return { code, message: String(message || "Selection is not eligible for cleanup commands.") };
}

function hasControlCharacters(value) {
  return /[\u0000-\u001F\u007F]/.test(String(value || ""));
}

function pathDepth(path) {
  return String(path || "").split("/").filter(Boolean).length;
}

function isAbsoluteCleanupPath(path) {
  return typeof path === "string" && path.startsWith("/");
}

function isCanonicalCleanupPath(path) {
  if (!isAbsoluteCleanupPath(path)) return false;
  if (path === "/") return true;
  if (hasControlCharacters(path)) return false;
  if (path.endsWith("/")) return false;
  const segments = path.split("/");
  for (let i = 1; i < segments.length; i += 1) {
    const segment = segments[i];
    if (!segment || segment === "." || segment === "..") return false;
  }
  return true;
}

function cleanupKindAllowed(kind) {
  return kind === "file" || kind === "directory";
}

function cleanupCandidateFromParts(item, source) {
  return {
    path: item && item.path,
    kind: item && item.kind,
    bytes: Number(item && item.bytes) || 0,
    owner: String((item && item.owner) || ""),
    source: source || (item && item.source) || "",
  };
}

function cleanupSelectedRoots(snapshot) {
  return Array.isArray(snapshot && snapshot.selected_roots) ? snapshot.selected_roots : [];
}

function cleanupMounts(snapshot) {
  return Array.isArray(snapshot && snapshot.mounts) ? snapshot.mounts : [];
}

function cleanupPathWithinRoot(path, root) {
  if (!path || !root) return false;
  if (root === "/") return String(path).startsWith("/");
  return path === root || path.startsWith(root + "/");
}

function cleanupLongestMatchingRoot(roots, path) {
  let match = null;
  for (const root of roots || []) {
    const scanRoot = root && root.scan_root;
    if (!isCanonicalCleanupPath(scanRoot)) continue;
    if (!cleanupPathWithinRoot(path, scanRoot)) continue;
    if (!match || scanRoot.length > match.scan_root.length) match = root;
  }
  return match;
}

function currentCleanupServerId() {
  if (typeof currentServerId !== "undefined" && currentServerId) return currentServerId;
  if (typeof globalThis !== "undefined" && globalThis.currentServerId) return globalThis.currentServerId;
  return null;
}

function currentCleanupSnapshot() {
  if (typeof DATA !== "undefined" && DATA) return DATA;
  if (typeof globalThis !== "undefined" && globalThis.DATA) return globalThis.DATA;
  return null;
}

function currentCleanupContext() {
  return {
    serverId: currentCleanupServerId(),
    snapshot: currentCleanupSnapshot(),
  };
}

function validateCleanupSelection(snapshot, candidate) {
  const item = cleanupCandidateFromParts(candidate);
  const path = item.path;
  const kind = item.kind;

  if (kind == null || kind === "") {
    return { accepted: false, path, kind: null, reason: cleanupBoundedReason("missing_kind", "Snapshot kind is required. Only file and directory selections can produce cleanup commands.") };
  }
  if (!cleanupKindAllowed(kind)) {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("unsupported_kind", "Only snapshot paths typed as file or directory are eligible. Symlink, other, and unknown kinds are copy-only display rows.") };
  }
  if (hasControlCharacters(path)) {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("control_character", "Snapshot paths with control characters are rejected.") };
  }
  if (!isCanonicalCleanupPath(path)) {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("noncanonical_path", "Only canonical absolute POSIX snapshot paths are eligible.") };
  }
  if (path === "/") {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("root_path", "The filesystem root cannot be used for cleanup commands.") };
  }

  const selectedRoots = cleanupSelectedRoots(snapshot);
  const mounts = cleanupMounts(snapshot);

  const rootByScan = selectedRoots.find(root => root && root.scan_root === path);
  if (rootByScan) {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("scan_root", "Selected scan roots themselves are never cleanup targets.") };
  }

  const rootByMount = selectedRoots.find(root => root && (root.mountpoint === path || root.mount_root === path));
  if (rootByMount) {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("mount_root", "Mount roots themselves are never cleanup targets.") };
  }

  const mountMatch = mounts.find(mount => mount && (mount.path === path || mount.scan_root === path));
  if (mountMatch) {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("mount_root", "Mount roots themselves are never cleanup targets.") };
  }

  if (pathDepth(path) <= 1) {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("top_level_root", "One-segment top-level paths are too broad for cleanup commands.") };
  }

  const matchingRoot = cleanupLongestMatchingRoot(selectedRoots, path);
  if (!matchingRoot) {
    return { accepted: false, path, kind, reason: cleanupBoundedReason("outside_selected_roots", "The snapshot path is outside the selected scan roots for this server.") };
  }
  if (matchingRoot.status !== "complete" && matchingRoot.status !== "partial") {
    return {
      accepted: false,
      path,
      kind,
      reason: cleanupBoundedReason("selected_root_status", "Only completed or partial selected scan roots may produce cleanup commands."),
      selectedRoot: matchingRoot,
    };
  }

  return {
    accepted: true,
    path,
    kind,
    bytes: item.bytes,
    owner: item.owner,
    source: item.source,
    scanRoot: matchingRoot.scan_root,
    selectedRoot: matchingRoot,
  };
}

function buildCleanupCommandPlan(snapshot, candidate) {
  const validation = validateCleanupSelection(snapshot, candidate);
  if (!validation.accepted) {
    return {
      accepted: false,
      path: validation.path || "",
      kind: validation.kind || "",
      reason: validation.reason,
      destructiveCommand: null,
    };
  }

  const quotedPath = shellQuote(validation.path);
  const destructiveCommand = {
    id: validation.kind === "file" ? "rm-file" : "rm-directory",
    label: validation.kind === "file" ? "Remove file" : "Remove directory",
    command: validation.kind === "file"
      ? "sudo rm -i -- " + quotedPath
      : "sudo rm -ri --one-file-system -- " + quotedPath,
  };

  return {
    accepted: true,
    path: validation.path,
    kind: validation.kind,
    scanRoot: validation.scanRoot,
    selectedRoot: validation.selectedRoot,
    destructiveCommand,
  };
}

function cleanupSelectionSummary(plan, item) {
  const kindLabel = plan.kind === "directory" ? "Directory" : "File";
  return kindLabel + " · " + plan.path + " · " + cleanupBytesLabel(item && item.bytes);
}

function cleanupSelectionButtonHtml(file, source) {
  const snapshot = currentCleanupSnapshot();
  const item = cleanupCandidateFromParts(file, source);
  if (!snapshot) return '<span class="hint" title="Snapshot data is not loaded yet">—</span>';
  const validation = validateCleanupSelection(snapshot, item);
  if (!validation.accepted) {
    return '<span class="hint" title="' + htmlEscape(validation.reason.message) + '">—</span>';
  }
  const selected = isCleanupSelectedPath(validation.path) ? " is-selected" : "";
  const pressed = isCleanupSelectedPath(validation.path) ? "true" : "false";
  const label = (validation.kind === "directory" ? "Select directory " : "Select file ") + validation.path;
  return '<button type="button" class="cleanup-select-btn' + selected + '"' +
    ' data-cleanup-path="' + htmlEscape(validation.path) + '"' +
    ' data-cleanup-kind="' + htmlEscape(validation.kind) + '"' +
    ' data-cleanup-bytes="' + htmlEscape(item.bytes) + '"' +
    ' data-cleanup-owner="' + htmlEscape(item.owner) + '"' +
    ' data-cleanup-source="' + htmlEscape(item.source) + '"' +
    ' aria-pressed="' + pressed + '"' +
    ' aria-label="' + htmlEscape(label) + '">Select</button>';
}

function cleanupCheckboxHtml(file, source) {
  return cleanupSelectionButtonHtml(file, source);
}

function cleanupItemFromControl(input) {
  const ds = input && input.dataset ? input.dataset : {};
  return cleanupCandidateFromParts({
    path: ds.cleanupPath || ds.path || "",
    kind: ds.cleanupKind || ds.kind || "",
    bytes: Number(ds.cleanupBytes || ds.bytes) || 0,
    owner: ds.cleanupOwner || ds.owner || "",
    source: ds.cleanupSource || ds.source || "",
  });
}

function currentCleanupPlans() {
  const ctx = currentCleanupContext();
  if (!ctx.snapshot) return [];
  if (cleanupSelectionState.serverId && ctx.serverId && cleanupSelectionState.serverId !== ctx.serverId) return [];
  return cleanupItems().map(item => ({
    item,
    plan: buildCleanupCommandPlan(ctx.snapshot, item),
  })).filter(entry => entry.plan.accepted);
}

function currentCleanupPlan() {
  const entries = currentCleanupPlans();
  return entries.length === 1 ? entries[0].plan : null;
}

function cleanupItems() {
  return Array.isArray(cleanupSelectionState.items) ? cleanupSelectionState.items.slice() : [];
}

function isCleanupSelectedPath(path) {
  return cleanupSelectionState.serverId === currentCleanupServerId() && cleanupItems().some(item => item.path === path);
}

function setCleanupSelectedItem(item, selected) {
  const ctx = currentCleanupContext();
  if (!ctx.snapshot || !item) return false;

  const validation = validateCleanupSelection(ctx.snapshot, item);
  if (!validation.accepted) return false;

  const nextItem = {
    path: validation.path,
    kind: validation.kind,
    bytes: Number(item.bytes) || 0,
    owner: String(item.owner || ""),
    source: String(item.source || ""),
  };
  if (cleanupSelectionState.serverId !== ctx.serverId) cleanupSelectionState.items = [];
  const items = cleanupItems();
  const existingIndex = items.findIndex(existing => existing.path === validation.path && existing.kind === validation.kind);
  const same = existingIndex >= 0;

  if (selected === false) {
    if (same) {
      cleanupSelectionState.serverId = ctx.serverId;
      items.splice(existingIndex, 1);
      cleanupSelectionState.items = items;
      renderCleanupPanel();
    }
    return false;
  }

  cleanupSelectionState.serverId = ctx.serverId;
  if (!same) {
    items.push(nextItem);
    cleanupSelectionState.items = items;
  }
  renderCleanupPanel();
  return true;
}

function toggleCleanupSelectedItem(item) {
  if (!item) return false;
  if (isCleanupSelectedPath(item.path)) {
    return setCleanupSelectedItem(item, false);
  }
  return setCleanupSelectedItem(item, true);
}

function resetCleanupSelectionState() {
  cleanupSelectionState.serverId = currentCleanupServerId();
  cleanupSelectionState.items = [];
  renderCleanupPanel();
}

function commandCardHtml(entry, copyLabel) {
  return '<li class="cleanup-command">' +
    '<div class="cleanup-command-head">' +
      '<div class="cleanup-command-title">' + htmlEscape(entry.label) + '</div>' +
      '<button type="button" class="cleanup-copy-btn" data-copy-command="' + htmlEscape(entry.command) + '"' +
        ' aria-label="' + htmlEscape(copyLabel + " " + entry.label) + '">Copy</button>' +
    '</div>' +
    '<pre class="cleanup-command-code"><code>' + htmlEscape(entry.command) + '</code></pre>' +
  '</li>';
}

function cleanupRemovalScript(entries) {
  return (entries || [])
    .map(entry => entry && entry.plan ? entry.plan.destructiveCommand : entry && entry.destructiveCommand)
    .filter(Boolean)
    .map(command => command.command)
    .join("\n");
}

function renderCleanupPanel() {
  const panel = typeof document !== "undefined" ? document.getElementById("cleanupPanel") : null;
  const entries = currentCleanupPlans();
  const singlePlan = entries.length === 1 ? entries[0].plan : null;
  const result = singlePlan || (entries.length ? {
    accepted: true,
    items: entries.map(entry => entry.item),
    plans: entries.map(entry => entry.plan),
  } : null);
  if (!panel) return result;

  const title = document.getElementById("cleanupSummary");
  const dangerCommand = document.getElementById("cleanupDangerCommand");
  const clear = document.getElementById("cleanupClear");

  const hasSelection = entries.length > 0;
  panel.classList.toggle("show", hasSelection);
  panel.setAttribute("aria-hidden", hasSelection ? "false" : "true");

  if (!hasSelection) {
    if (title) title.textContent = "No path selected";
    if (dangerCommand) dangerCommand.innerHTML = "";
    if (clear) clear.disabled = true;
    return result;
  }

  if (title) {
    if (entries.length === 1) title.textContent = cleanupSelectionSummary(entries[0].plan, entries[0].item);
    else {
      const totalBytes = entries.reduce((sum, entry) => sum + (Number(entry.item.bytes) || 0), 0);
      title.textContent = entries.length + " paths selected · " + cleanupBytesLabel(totalBytes);
    }
  }
  if (clear) clear.disabled = false;
  if (dangerCommand) {
    dangerCommand.innerHTML = commandCardHtml({
      label: entries.length === 1 ? entries[0].plan.destructiveCommand.label : "Remove selected paths",
      command: cleanupRemovalScript(entries),
    }, "Copy removal command:");
  }

  if (typeof document !== "undefined" && document.dispatchEvent && typeof CustomEvent !== "undefined") {
    document.dispatchEvent(new CustomEvent("cleanup-selection-rendered", {
      detail: { item: entries.length === 1 ? entries[0].item : null, items: entries.map(entry => entry.item), plan: result },
    }));
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
  if (typeof document === "undefined") return;
  if (!document._cleanupSelectionBound) {
    document._cleanupSelectionBound = true;
    document.addEventListener("click", async (e) => {
      const selectBtn = e.target && e.target.closest ? e.target.closest(".cleanup-select-btn") : null;
      if (selectBtn) {
        e.preventDefault();
        toggleCleanupSelectedItem(cleanupItemFromControl(selectBtn));
        return;
      }

      const clear = e.target && e.target.closest ? e.target.closest("#cleanupClear") : null;
      if (clear) {
        e.preventDefault();
        resetCleanupSelectionState();
        return;
      }

      const copyBtn = e.target && e.target.closest ? e.target.closest(".cleanup-copy-btn") : null;
      if (copyBtn) {
        e.preventDefault();
        try {
          const ok = await writeClipboardText(copyBtn.getAttribute("data-copy-command"));
          setButtonFeedback(copyBtn, ok ? "Copied" : "Copy failed", ok ? "done" : "");
        } catch (_) {
          setButtonFeedback(copyBtn, "Copy failed", "");
        }
      }
    });
  }
  renderCleanupPanel();
}

if (typeof globalThis !== "undefined") {
  globalThis.cleanupSelectionState = cleanupSelectionState;
  globalThis.shellQuote = shellQuote;
  globalThis.shellQuotePath = shellQuotePath;
  globalThis.cleanupCheckboxHtml = cleanupCheckboxHtml;
  globalThis.cleanupSelectionButtonHtml = cleanupSelectionButtonHtml;
  globalThis.cleanupItems = cleanupItems;
  globalThis.validateCleanupSelection = validateCleanupSelection;
  globalThis.buildCleanupCommandPlan = buildCleanupCommandPlan;
  globalThis.cleanupRemovalScript = cleanupRemovalScript;
  globalThis.renderCleanupPanel = renderCleanupPanel;
  globalThis.bindCleanupSelection = bindCleanupSelection;
  globalThis.isCleanupSelectedPath = isCleanupSelectedPath;
  globalThis.setCleanupSelectedItem = setCleanupSelectedItem;
  globalThis.toggleCleanupSelectedItem = toggleCleanupSelectedItem;
  globalThis.resetCleanupSelectionState = resetCleanupSelectionState;
  globalThis.isAbsoluteCleanupPath = isAbsoluteCleanupPath;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    shellQuote,
    shellQuotePath,
    cleanupCheckboxHtml,
    cleanupSelectionButtonHtml,
    cleanupItems,
    validateCleanupSelection,
    buildCleanupCommandPlan,
    cleanupRemovalScript,
    renderCleanupPanel,
    bindCleanupSelection,
    isCleanupSelectedPath,
    setCleanupSelectedItem,
    toggleCleanupSelectedItem,
    resetCleanupSelectionState,
    isAbsoluteCleanupPath,
    isCanonicalCleanupPath,
    cleanupPathWithinRoot,
    cleanupLongestMatchingRoot,
    pathDepth,
  };
}
