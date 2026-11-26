import {pathRoundTrips} from './api.js';
import {humanBytes} from './format.js';
function shellQuote(path) {
  return "'" + String(path).replace(/'/g, "'\"'\"'") + "'";
}

function cleanupBoundedReason(code, message) {
  return { code, message: String(message || "Selection is not eligible for cleanup commands.") };
}

function hasControlCharacters(value) {
  const text = String(value || "");
  return /[\u0000-\u001F\u007F-\u009F\u200E\u200F\u202A-\u202E\u2066-\u2069]/.test(text) ||
    (typeof text.isWellFormed === "function" && !text.isWellFormed());
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
  if (hasControlCharacters(path) || path.includes("\uFFFD")) return false;
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
  if (!pathRoundTrips(path,candidate?.path_b64)) {
    return {accepted:false,path,kind,reason:cleanupBoundedReason("raw_path","Displayed path does not match the original bytes.")};
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

  if (kind === "directory" && snapshot.mount_table) {
    const table = snapshot.mount_table;
    if (table.status === "unreadable" || (table.within_roots || []).some(m => cleanupPathWithinRoot(m.mountpoint ?? m.path ?? m, path))) {
      return {accepted:false, path, kind, reason:cleanupBoundedReason("mount_guard", "Mounts under this directory could not be ruled out.")};
    }
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
      ? "sudo rm -f -- " + quotedPath
      : "sudo rm -rf --one-file-system --preserve-root=all -- " + quotedPath,
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
  return kindLabel + " · " + plan.path + " · " + humanBytes(item?.bytes || 0);
}

function cleanupEffectiveEntries(entries) {
  const source = (entries || []).filter(Boolean);
  const planOf = entry => entry && entry.plan ? entry.plan : entry;
  const directoryPaths = source
    .map(planOf)
    .filter(plan => plan && plan.kind === "directory" && isCanonicalCleanupPath(plan.path))
    .map(plan => plan.path);
  const seen = new Set();
  let nested = 0;
  const kept = source.filter(entry => {
    const plan = planOf(entry);
    const path = plan && plan.path;
    if (!path || seen.has(path)) return false;
    seen.add(path);
    if (directoryPaths.some(directoryPath => directoryPath !== path && cleanupPathWithinRoot(path, directoryPath))) {
      nested += 1;
      return false;
    }
    return !!(plan.destructiveCommand && isCanonicalCleanupPath(plan.path));
  });
  return { kept, nested };
}

function cleanupRemovalScript(snapshot, entries) {
  const kept = cleanupEffectiveEntries(entries).kept.map(entry => entry && entry.plan ? entry.plan : entry);
  const quoted = kind => kept.filter(plan => plan.kind === kind).map(plan => shellQuote(plan.path)).join(" ");
  const commands = [];
  if (quoted("directory")) commands.push("sudo rm -rf --one-file-system --preserve-root=all -- " + quoted("directory"));
  if (quoted("file")) commands.push("sudo rm -f -- " + quoted("file"));
  if (!commands.length) return "";
  let body = commands.join("; ");
  if (quoted("directory")) {
    const mountinfoQuoted = kept.filter(plan => plan.kind === "directory")
      .map(plan => shellQuote(plan.path.replace(/[\\ \t\n]/g, c => "\\" + c.charCodeAt(0).toString(8).padStart(3, "0"))))
      .join(" ");
    body = "sv_ok=1; for sv_p in " + mountinfoQuoted + "; do while read -r _ _ _ _ sv_m _; do " +
      "case \"$sv_m\" in \"$sv_p\"|\"$sv_p\"/*) echo \"storage-viz: $sv_p has a mount at $sv_m; nothing was removed\" >&2; sv_ok=0;; esac; " +
      "done < /proc/self/mountinfo; done; [ \"$sv_ok\" = 1 ] && { " + body + "; }";
  }
  const host = (snapshot?.host?.hostname ?? snapshot?.hostname ?? "").split(".")[0];
  if (!host || hasControlCharacters(host)) return "";
  return "if [ \"$(hostname -s)\" = " + shellQuote(host) + " ]; then " + body +
    "; else echo 'storage-viz: wrong server, nothing was removed. These paths belong to:' " + shellQuote(host) + " >&2; fi";
}


export function removal(detail, items) {
  const entries = items.map(item => ({item, plan: buildCleanupCommandPlan(detail, item)}));
  const refused = entries.find(({item,plan}) => item.flag || !plan.accepted);
  const {kept, nested} = cleanupEffectiveEntries(entries);
  const bytes = kept.reduce((sum, {item}) => sum + (item.bytes || 0), 0);
  const summary = entries.length === 1 ? cleanupSelectionSummary(entries[0].plan,items[0])
    : entries.length + " paths selected · " + (nested ? nested + " inside a selected folder · " : "") + humanBytes(bytes);
  const command = refused ? "" : cleanupRemovalScript(detail, entries);
  return {command, summary, refusal: refused ? refused.item.flag || refused.plan.reason.message : null};
}
export {cleanupRemovalScript, buildCleanupCommandPlan, validateCleanupSelection, cleanupLongestMatchingRoot, cleanupPathWithinRoot, isCanonicalCleanupPath, shellQuote};
