// @ts-nocheck
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../vite.config.ts", import.meta.url), "utf8");

test("client resolution keeps Vite browser conditions when adding the Svelte condition", () => {
  assert.match(source, /resolve\s*:\s*\{/);
  assert.match(source, /conditions\s*:\s*\[[^\]]*["']svelte["'][^\]]*["']browser["'][^\]]*["']module["'][^\]]*["']development\|production["']/s);
});
