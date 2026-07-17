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

function testAdvisorHelpers() {
  const client = require("./advisor-client.js");
  const badges = require("./advisor-badges.js");
  const recs = [
    { id: "cache", action: "delete", category: "pip-cache", target_path: "/home/a/.cache/pip", suggested_next_step: "review-delete-command", badge: "AI: cache cleanup" },
    { id: "move", action: "move", category: "checkpoint", target_path: "/ssd/a/run/ckpt.pt", suggested_next_step: "move-to-hdd", badge: "AI: move" },
  ];
  assert.strictEqual(client.isDeleteSelectableRecommendation(recs[0]), true, "delete review recommendations can enter cleanup selection");
  assert.strictEqual(client.isDeleteSelectableRecommendation(recs[1]), false, "move recommendations must not generate rm commands");
  const filtered = client.filterExcludedRecommendations(recs, [
    { type: "action", action: "move" },
    { type: "path", path: "/home/a/.cache/pip" },
  ]);
  assert.deepStrictEqual(filtered, [], "path and action exclusions should hide matching recommendations");
  assert.strictEqual(badges.recommendationMatchesPath(recs[0], "/home/a/.cache/pip/wheels/pkg.whl"), true);
  assert.strictEqual(badges.recommendationMatchesPath(recs[0], "/home/a/other"), false);
  const byPath = badges.recommendationsForPath(recs, "/ssd/a/run/ckpt.pt");
  assert.deepStrictEqual(byPath.map(r => r.id), ["move"]);
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

function testAdvisorBadgeEscapingUsesSharedEscaperWithoutRecursion() {
  const badges = require("./advisor-badges.js");
  const oldHtmlEscape = global.htmlEscape;
  global.htmlEscape = value => "escaped:" + String(value).replace(/</g, "&lt;");
  try {
    badges.advisorSetRecommendations({
      recommendations: [
        { id: "x<1", action: "archive", target_path: "/data/archive.tar", badge: "AI: <archive>", reason_short: "큰 파일" },
      ],
    });
    const html = badges.advisorBadgesHtml("/data/archive.tar");
    assert(html.includes("escaped:x&lt;1"), "shared htmlEscape should be used for data attributes");
    assert(html.includes("escaped:AI: &lt;archive>"), "shared htmlEscape should be used for labels");
  } finally {
    if (oldHtmlEscape === undefined) delete global.htmlEscape;
    else global.htmlEscape = oldHtmlEscape;
  }
}



function testAdvisorGlobalAndCrossSurfaceContracts() {
  const client = require("./advisor-client.js");
  const badges = require("./advisor-badges.js");
  const payload = client.normalizeAdvisorPayload({
    host_id: "hinton",
    summary: { health: "critical", headline: "한국어 요약", top_drivers: ["AI: cache: /data/cache"] },
    recommendations: [
      { id: "cache", action: "delete", category: "pip-cache", target_path: "/data/cache", bytes: 100, risk: "low", suggested_next_step: "review-delete-command", badge: "AI: 캐시 정리", reason_short: "pip 캐시가 큽니다.", reason_detail: "다시 받을 수 있는 캐시입니다." },
      { id: "move", action: "move", category: "checkpoint", target_path: "/data/logs/run.ckpt", bytes: 50, risk: "medium", suggested_next_step: "move-to-hdd", badge: "AI: HDD 이동", reason_short: "SSD 압박입니다.", reason_detail: "HDD로 옮기세요." },
    ],
  }, []);
  assert.strictEqual(client.advisorOutputLanguage(payload), "ko", "final advisor output must be Korean-facing");
  const grouped = client.groupAdvisorRecommendations(payload.recommendations);
  assert.deepStrictEqual(grouped.map(g => g.action), ["delete", "move"], "recommendations should be grouped by action for compact UI");
  badges.advisorSetRecommendations(payload);
  assert.strictEqual(badges.advisorRecommendationsForPath("/data/cache/wheels/a.whl").length, 1, "top/stale descendant paths get AI badges");
  assert.strictEqual(badges.advisorRecommendationsForPath("/data/logs/run.ckpt").length, 1, "file path rows get AI badges");
}

async function testAdvisorRunIsGlobalNotTabScoped() {
  const client = require("./advisor-client.js");
  const oldFetch = global.fetch;
  const oldRender = global.renderAdvisorPanel;
  const oldRefresh = global.refreshAdvisorBadges;
  let rendered = 0, refreshed = 0;
  global.renderAdvisorPanel = () => { rendered++; };
  global.refreshAdvisorBadges = () => { refreshed++; };
  global.fetch = async (url) => {
    assert.strictEqual(String(url), "/ai/recommend");
    return { ok: true, json: async () => ({
      schema_version: 1, host_id: "hinton", mode: "rule+llm",
      summary: { health: "warning", headline: "한국어", top_drivers: [] },
      recommendations: [{ id: "x", action: "archive", category: "archive", target_path: "/data/x.tar", badge: "AI: 보관 검토", reason_short: "큰 압축 파일입니다.", suggested_next_step: "inspect-owner" }],
    }) };
  };
  try {
    const payload = await client.runAdvisor({ hostId: "hinton", max_items: 1 });
    assert.strictEqual(payload.mode, "rule+llm");
    assert.strictEqual(client.advisorState.recommendations.length, 1);
    assert(rendered > 0, "global run should update compact advisor surfaces");
    assert(refreshed > 0, "global run should refresh treemap/top/stale badges");
  } finally {
    global.fetch = oldFetch;
    global.renderAdvisorPanel = oldRender;
    global.refreshAdvisorBadges = oldRefresh;
  }
}

async function testAdvisorFallbackWarningIsNotHardError() {
  const client = require("./advisor-client.js");
  const oldFetch = global.fetch;
  const oldRender = global.renderAdvisorPanel;
  const oldRefresh = global.refreshAdvisorBadges;
  global.renderAdvisorPanel = () => {};
  global.refreshAdvisorBadges = () => {};
  global.fetch = async (url) => {
    assert.strictEqual(String(url), "/ai/recommend");
    return { ok: true, json: async () => ({
      schema_version: 1,
      host_id: "hinton",
      mode: "rule-only",
      output_language: "ko",
      advisor_error: "LLM synthesis unavailable; rule-only recommendations shown: connection refused",
      summary: { health: "warning", headline: "AI 정리 추천이 있습니다", top_drivers: [] },
      recommendations: [{ id: "x", action: "archive", category: "archive", target_path: "/data/x.tar", badge: "AI: 보관 검토", reason_short: "큰 압축 파일입니다.", suggested_next_step: "inspect-owner" }],
    }) };
  };
  try {
    await client.runAdvisor({ hostId: "hinton", max_items: 1 });
    assert.strictEqual(client.advisorState.error, null, "rule-only fallback with recommendations must not render as hard AI error");
    assert.match(client.advisorState.warning, /LLM synthesis unavailable/, "LLM connection issue should be a warning");
    assert.strictEqual(client.advisorState.recommendations.length, 1);
  } finally {
    global.fetch = oldFetch;
    global.renderAdvisorPanel = oldRender;
    global.refreshAdvisorBadges = oldRefresh;
    client.advisorState.warning = null;
  }
}

async function testAdvisorStatusFallsBackToSidecarPort() {
  const client = require("./advisor-client.js");
  const oldFetch = global.fetch;
  const oldLocation = global.location;
  const calls = [];
  global.location = { protocol: "http:", hostname: "127.0.0.1", port: "8088", origin: "http://127.0.0.1:8088" };
  global.fetch = async (url) => {
    calls.push(String(url));
    if (String(url) === "/ai/status") return { ok: false, status: 404, json: async () => ({}) };
    if (String(url) === "http://127.0.0.1:18089/ai/status") {
      return { ok: true, json: async () => ({ enabled: true, provider: "mock", model: "qwen2.5:14b", message: "AI Advisor is enabled." }) };
    }
    throw new Error("unexpected URL " + url);
  };
  try {
    const status = await client.fetchAdvisorStatus();
    assert.strictEqual(status.enabled, true, "advisor status should fall back to sidecar AI server when current 8088 server lacks /ai/status");
    assert.deepStrictEqual(calls, ["/ai/status", "http://127.0.0.1:18089/ai/status"]);
  } finally {
    global.fetch = oldFetch;
    global.location = oldLocation;
  }
}

async function testAdvisorLatestCacheLoadsIntoCrossSurfaceState() {
  const client = require("./advisor-client.js");
  const badges = require("./advisor-badges.js");
  const oldFetch = global.fetch;
  const oldRender = global.renderAdvisorPanel;
  const oldRefresh = global.refreshAdvisorBadges;
  let rendered = 0, refreshed = 0;
  global.renderAdvisorPanel = () => { rendered++; };
  global.refreshAdvisorBadges = () => { refreshed++; };
  global.fetch = async (url) => {
    assert.strictEqual(String(url), "/ai/latest?host_id=hinton");
    return { ok: true, json: async () => ({
      schema_version: 1,
      host_id: "hinton",
      mode: "mock",
      output_language: "ko",
      summary: { health: "warning", headline: "캐시된 AI 추천", top_drivers: [] },
      recommendations: [
        { id: "cached-cache", action: "delete", category: "pip-cache", target_path: "/data/cache", badge: "AI: 캐시 정리", reason_short: "캐시입니다.", suggested_next_step: "review-delete-command" },
      ],
    }) };
  };
  try {
    const payload = await client.loadAdvisorLatest({ hostId: "hinton" });
    assert.strictEqual(payload.mode, "mock");
    assert.strictEqual(client.advisorState.recommendations.length, 1, "latest cached advisor payload populates global state");
    assert.strictEqual(badges.advisorRecommendationsForPath("/data/cache/wheel.whl").length, 1, "cached latest recommendations show on cross-surface badges");
    assert(rendered > 0, "latest load should refresh advisor panel");
    assert(refreshed > 0, "latest load should refresh treemap/top/stale badges");
  } finally {
    global.fetch = oldFetch;
    global.renderAdvisorPanel = oldRender;
    global.refreshAdvisorBadges = oldRefresh;
  }
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

async function main() {
  testHostManifest();
  testHostManifestHelpers();
  testAdvisorHelpers();
  testTreemapFidelity();
  testDeleteCommandGeneration();
  testAdvisorClientFilteringAndBadges();
  testAdvisorBadgeEscapingUsesSharedEscaperWithoutRecursion();
  testAdvisorGlobalAndCrossSurfaceContracts();
  await testAdvisorRunIsGlobalNotTabScoped();
  await testAdvisorFallbackWarningIsNotHardError();
  await testAdvisorStatusFallsBackToSidecarPort();
  await testAdvisorLatestCacheLoadsIntoCrossSurfaceState();
  console.log("viewer regression tests passed");
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
