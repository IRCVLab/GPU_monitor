"use strict";

/* Selection/delete-command state + helpers.
   Copy-only: this module generates commands but never executes deletion. */
var cleanupSelected = (typeof globalThis !== "undefined" && globalThis.cleanupSelected) ||
  (typeof Map !== "undefined" ? new Map() : null);

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

if (typeof globalThis !== "undefined") {
  globalThis.cleanupSelected = cleanupSelected;
  globalThis.shellQuote = shellQuote;
  globalThis.shellQuotePath = shellQuotePath;
  globalThis.buildDeleteCommands = buildDeleteCommands;
  globalThis.buildDeleteCommandsSafe = buildDeleteCommands;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { shellQuote, shellQuotePath, buildDeleteCommands, isAbsoluteCleanupPath, dedupeDescendants };
}
