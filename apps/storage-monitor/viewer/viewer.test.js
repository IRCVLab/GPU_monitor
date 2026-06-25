#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const here = __dirname;

function testHostManifest() {
  const manifestPath = path.join(here, "..", "data", "hosts.json");
  const hosts = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  assert(Array.isArray(hosts), "hosts manifest must be an array");
  assert(hosts.some(h => h.id === "hinton" && h.file === "hinton"), "hinton host entry must exist");
  for (const h of hosts) {
    assert(/^[A-Za-z0-9._-]+$/.test(h.id), `host id is safe: ${h.id}`);
    assert(h.label && h.file, `host has label and file: ${JSON.stringify(h)}`);
  }
}

function testHostManifestHelpers() {
  const { normalizeHosts } = require("./data-client.js");
  const hosts = normalizeHosts([
    { id: "hinton", label: "Hinton", file: "hinton", default: true },
    { id: "../bad", label: "bad", file: "../bad" },
    { id: "lecun", file: "lecun" },
  ]);
  assert.deepStrictEqual(hosts.map(h => h.id), ["hinton", "lecun"]);
  assert.strictEqual(hosts[0].label, "Hinton");
  assert.strictEqual(hosts[1].label, "lecun");
  assert.strictEqual(hosts[0].default, true);
  assert.deepStrictEqual(normalizeHosts([]), [{ id: "hinton", label: "hinton", file: "hinton", default: true }]);
}

function testTreemapFidelity() {
  const { squarify, rectArea } = require("./treemap.js");
  const gib = 1024 ** 3;
  const rects = squarify([
    { id: "large", value: 600 * gib },
    { id: "small", value: 600 * 1024 ** 2 },
  ], 0, 0, 1000, 600);
  const large = rects.find(r => r.id === "large");
  const small = rects.find(r => r.id === "small");
  assert(large && small, "both sibling rects are present");
  const ratio = rectArea(large) / rectArea(small);
  assert(ratio > 900 && ratio < 1100, `600GB/600MB area ratio should remain ~1024, got ${ratio}`);
}


function testAdvisorClientFilteringAndBadges() {
  const client = require("./advisor-client.js");
  const badges = require("./advisor-badges.js");
  const ui = require("./advisor-ui.js");
  const payload = client.normalizeAdvisorPayload({
    host_id: "hinton",
    summary: { health: "warning", headline: "review", top_drivers: [] },
    recommendations: [
      { id: "delete-cache", action: "delete", category: "pip-cache", target_path: "/data/cache", bytes: 100, risk: "low", suggested_next_step: "review-delete-command", badge: "AI: cache" },
      { id: "move-log", action: "move", category: "log", target_path: "/data/logs", bytes: 50, risk: "medium", suggested_next_step: "move-to-hdd", badge: "AI: move" },
    ],
  }, { items: [{ type: "action", action: "move" }] });
  assert.deepStrictEqual(payload.recommendations.map(r => r.id), ["delete-cache"], "action exclusions must filter advisor payloads");

  badges.advisorSetRecommendations(payload);
  assert.strictEqual(badges.advisorRecommendationsForPath("/data/cache/wheel.whl").length, 1, "badges match descendant paths");
  const html = badges.advisorBadgesHtml("/data/cache/wheel.whl");
  assert(html.includes("AI: cache"), "badge HTML includes recommendation label");
  assert(!html.includes("checkbox"), "advisor badges must not add checkbox clutter");

  assert.strictEqual(ui.isAdvisorSafeDeleteRecommendation(payload.recommendations[0]), true, "safe delete rec can connect to cleanup selection");
  assert.strictEqual(ui.isAdvisorSafeDeleteRecommendation({ action: "move", target_path: "/data/logs", suggested_next_step: "move-to-hdd" }), false, "move recs cannot become delete commands");
  assert.strictEqual(ui.isAdvisorSafeDeleteRecommendation({ action: "delete", target_path: "/home", suggested_next_step: "review-delete-command" }), false, "top-level delete recs are guarded");
}

function testDeleteCommandGeneration() {
  const { shellQuote, buildDeleteCommands } = require("./selection.js");
  assert.strictEqual(shellQuote("/data/a b/it's.txt"), "'/data/a b/it'\"'\"'s.txt'");
  const result = buildDeleteCommands([
    { path: "/data/parent", bytes: 100 },
    { path: "/data/parent/child.bin", bytes: 50 },
    { path: "relative/path", bytes: 1 },
    { path: "/data/other file", bytes: 2 },
  ]);
  assert.deepStrictEqual(result.commands, [
    "sudo rm -rf -- '/data/parent'",
    "sudo rm -rf -- '/data/other file'",
  ]);
  assert(result.warnings.some(w => w.includes("relative/path")), "relative paths are warned");
  assert(result.warnings.some(w => w.includes("/data/parent/child.bin")), "descendant duplicates are warned");
}

testHostManifest();
testHostManifestHelpers();
testTreemapFidelity();
testDeleteCommandGeneration();
testAdvisorClientFilteringAndBadges();
console.log("viewer regression tests passed");
