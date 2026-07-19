"use strict";

/* Copy-only storage cleanup workflow.
   The browser may validate snapshot selections and copy fixed command templates,
   but it never executes filesystem commands. */

var cleanupSelectionState = (typeof globalThis !== "undefined" && globalThis.cleanupSelectionState) || {
  serverId: null,
  item: null,
  revealDestructive: false,
};

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

function cleanupWarning(message) {
  return String(message || "").slice(0, 240);
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

function cleanupSnapshotFreshness(options) {
  return String((options && options.freshness) || (options && options.summary && options.summary.freshness) || "unknown");
}

function currentCleanupServerId() {
  if (typeof currentServerId !== "undefined" && currentServerId) return currentServerId;
  if (typeof globalThis !== "undefined" && globalThis.currentServerId) return globalThis.currentServerId;
  return null;
}

function currentCleanupSummary() {
  if (typeof currentServerSummary !== "undefined" && currentServerSummary) return currentServerSummary;
  if (typeof globalThis !== "undefined" && globalThis.currentServerSummary) return globalThis.currentServerSummary;
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
    summary: currentCleanupSummary(),
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

function cleanupRetainedSnapshotWarning(options) {
  const freshness = cleanupSnapshotFreshness(options);
  const summary = options && options.summary;
  if (freshness === "stale") return "Snapshot may be stale or retained from an earlier scan. Confirm the live path before removing anything.";
  if (summary && summary.latest_pull_status && summary.latest_pull_status !== "succeeded") {
    return "This server may be showing retained snapshot data after a pull problem. Confirm the live path before removing anything.";
  }
  if (summary && summary.latest_scan_result && summary.latest_scan_result !== "complete" && summary.latest_scan_result !== "partial") {
    return "This server may be showing retained snapshot data after a scan problem. Confirm the live path before removing anything.";
  }
  return "";
}

function buildCleanupCommandPlan(snapshot, candidate, options) {
  const validation = validateCleanupSelection(snapshot, candidate);
  if (!validation.accepted) {
    return {
      accepted: false,
      path: validation.path || "",
      kind: validation.kind || "",
      reason: validation.reason,
      warnings: [validation.reason.message],
      inspectionCommands: [],
      destructiveCommand: null,
      destructiveVisible: false,
    };
  }

  const quotedPath = shellQuote(validation.path);
  const inspectionCommands = [
    {
      id: "du",
      label: "Check total size",
      command: "sudo du -shx -- " + quotedPath,
    },
    {
      id: "largest",
      label: "List largest descendants",
      command: "sudo find " + quotedPath + " -xdev \\( -type f -o -type d \\) -printf '%s\\t%TY-%Tm-%Td %TH:%TM\\t%p\\n' | sort -nr | head -n 20",
    },
    {
      id: "stat",
      label: "Inspect metadata",
      command: "sudo stat -- " + quotedPath,
    },
    {
      id: "mtime",
      label: "Review modification times",
      command: "sudo find " + quotedPath + " -xdev -printf '%TY-%Tm-%Td %TH:%TM\\t%s\\t%p\\n' | sort -r | head -n 20",
    },
  ];

  const destructiveCommand = {
    id: validation.kind === "file" ? "rm-file" : "rm-directory",
    label: validation.kind === "file" ? "Remove file" : "Remove directory",
    command: validation.kind === "file"
      ? "sudo rm -i -- " + quotedPath
      : "sudo rm -ri --one-file-system -- " + quotedPath,
  };

  const warnings = [
    cleanupWarning("Snapshot path may have changed on the live server since this scan. Review the copied command before running it."),
  ];
  if (validation.selectedRoot && validation.selectedRoot.status === "partial") {
    warnings.push(cleanupWarning("This selection came from a partial scan root. Some descendants may have been unreadable during the snapshot."));
  }
  const retainedSnapshotWarning = cleanupRetainedSnapshotWarning(options);
  if (retainedSnapshotWarning) warnings.push(cleanupWarning(retainedSnapshotWarning));

  return {
    accepted: true,
    path: validation.path,
    kind: validation.kind,
    scanRoot: validation.scanRoot,
    selectedRoot: validation.selectedRoot,
    warnings,
    inspectionCommands,
    destructiveCommand,
    destructiveVisible: !!(options && options.revealDestructive),
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
  const label = (validation.kind === "directory" ? "Inspect directory " : "Inspect file ") + validation.path;
  return '<button type="button" class="cleanup-select-btn' + selected + '"' +
    ' data-cleanup-path="' + htmlEscape(validation.path) + '"' +
    ' data-cleanup-kind="' + htmlEscape(validation.kind) + '"' +
    ' data-cleanup-bytes="' + htmlEscape(item.bytes) + '"' +
    ' data-cleanup-owner="' + htmlEscape(item.owner) + '"' +
    ' data-cleanup-source="' + htmlEscape(item.source) + '"' +
    ' aria-pressed="' + pressed + '"' +
    ' aria-label="' + htmlEscape(label) + '">Inspect</button>';
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

function currentCleanupPlan() {
  const ctx = currentCleanupContext();
  if (!cleanupSelectionState.item || !ctx.snapshot) return null;
  if (cleanupSelectionState.serverId && ctx.serverId && cleanupSelectionState.serverId !== ctx.serverId) return null;
  return buildCleanupCommandPlan(ctx.snapshot, cleanupSelectionState.item, {
    revealDestructive: cleanupSelectionState.revealDestructive,
    freshness: cleanupSnapshotFreshness({ summary: ctx.summary }),
    summary: ctx.summary,
  });
}

function cleanupItems() {
  return cleanupSelectionState.item ? [cleanupSelectionState.item] : [];
}

function isCleanupSelectedPath(path) {
  return !!(cleanupSelectionState.item && cleanupSelectionState.item.path === path && cleanupSelectionState.serverId === currentCleanupServerId());
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
  const same = cleanupSelectionState.serverId === ctx.serverId &&
    cleanupSelectionState.item &&
    cleanupSelectionState.item.path === validation.path &&
    cleanupSelectionState.item.kind === validation.kind;

  if (selected === false) {
    if (same) {
      cleanupSelectionState.serverId = ctx.serverId;
      cleanupSelectionState.item = null;
      cleanupSelectionState.revealDestructive = false;
      renderCleanupPanel();
    }
    return false;
  }

  cleanupSelectionState.serverId = ctx.serverId;
  cleanupSelectionState.item = nextItem;
  cleanupSelectionState.revealDestructive = same && selected === true
    ? cleanupSelectionState.revealDestructive
    : false;
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
  cleanupSelectionState.item = null;
  cleanupSelectionState.revealDestructive = false;
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

function scrollCleanupPanelControlIntoView(target) {
  if (!target || typeof target.scrollIntoView !== "function") return;
  target.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function renderCleanupPanel() {
  const panel = typeof document !== "undefined" ? document.getElementById("cleanupPanel") : null;
  const plan = currentCleanupPlan();
  if (!panel) return plan;

  const title = document.getElementById("cleanupSummary");
  const warnings = document.getElementById("cleanupWarnings");
  const inspectList = document.getElementById("cleanupInspectList");
  const reveal = document.getElementById("cleanupReveal");
  const dangerWarning = document.getElementById("cleanupDangerWarning");
  const dangerCommand = document.getElementById("cleanupDangerCommand");
  const clear = document.getElementById("cleanupClear");

  const hasSelection = !!(plan && plan.accepted);
  panel.classList.toggle("show", hasSelection);
  panel.setAttribute("aria-hidden", hasSelection ? "false" : "true");

  if (!hasSelection) {
    if (title) title.textContent = "No path selected";
    if (warnings) warnings.innerHTML = "";
    if (inspectList) inspectList.innerHTML = "";
    if (dangerWarning) {
      dangerWarning.hidden = true;
      dangerWarning.textContent = "";
    }
    if (dangerCommand) dangerCommand.innerHTML = "";
    if (reveal) reveal.hidden = true;
    if (clear) clear.disabled = true;
    return plan;
  }

  if (title) title.textContent = cleanupSelectionSummary(plan, cleanupSelectionState.item);
  if (warnings) warnings.innerHTML = plan.warnings.map(text => '<p class="cleanup-note">⚠ ' + htmlEscape(text) + "</p>").join("");
  if (inspectList) inspectList.innerHTML = plan.inspectionCommands.map(entry => commandCardHtml(entry, "Copy inspection command:")).join("");
  if (clear) clear.disabled = false;

  if (reveal) {
    reveal.hidden = false;
    reveal.disabled = plan.destructiveVisible;
    reveal.textContent = plan.destructiveVisible ? "Removal command revealed" : "Reveal removal command";
  }

  if (dangerWarning) {
    dangerWarning.hidden = !plan.destructiveVisible;
    dangerWarning.textContent = plan.destructiveVisible
      ? "Danger: review the live path carefully. The browser only copies this command; it never runs it."
      : "";
  }

  if (dangerCommand) {
    dangerCommand.innerHTML = plan.destructiveVisible
      ? commandCardHtml(plan.destructiveCommand, "Copy removal command:")
      : "";
  }
  if (plan.destructiveVisible) {
    scrollCleanupPanelControlIntoView(dangerCommand || dangerWarning || reveal);
  } else {
    scrollCleanupPanelControlIntoView(reveal);
  }

  if (typeof document !== "undefined" && document.dispatchEvent && typeof CustomEvent !== "undefined") {
    document.dispatchEvent(new CustomEvent("cleanup-selection-rendered", { detail: { item: cleanupSelectionState.item, plan } }));
  }
  return plan;
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

      const reveal = e.target && e.target.closest ? e.target.closest("#cleanupReveal") : null;
      if (reveal) {
        e.preventDefault();
        cleanupSelectionState.revealDestructive = true;
        renderCleanupPanel();
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
