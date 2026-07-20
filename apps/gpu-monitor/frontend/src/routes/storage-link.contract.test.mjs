import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pagePath = join(dirname(fileURLToPath(import.meta.url)), "+page.svelte");
const source = readFileSync(pagePath, "utf8");

const anchors = source.match(/<a\b[^>]*>[\s\S]*?<\/a>/gi) || [];
const storageAnchors = anchors.filter((anchor) => {
  const text = anchor
    .replace(/<script\b[\s\S]*?<\/script>/gi, "")
    .replace(/<style\b[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
  return text === "Storage";
});

if (storageAnchors.length === 0) {
  throw new Error("Expected +page.svelte to contain a Storage anchor.");
}

const storageAnchor = storageAnchors.find((anchor) => /\bhref\s*=\s*(["'])http:\/\/127\.0\.0\.1:8088\/\1/i.test(anchor));

if (!storageAnchor) {
  throw new Error("Expected Storage anchor href to be http://127.0.0.1:8088/.");
}

if (/\btarget\s*=\s*(["'])_blank\1/i.test(storageAnchor)) {
  throw new Error("Expected Storage anchor to open in the same tab, but target=\"_blank\" was found.");
}

console.log("Storage navigation contract passed.");
