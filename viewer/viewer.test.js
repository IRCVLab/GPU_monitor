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
testTreemapFidelity();
testDeleteCommandGeneration();
console.log("viewer regression tests passed");
