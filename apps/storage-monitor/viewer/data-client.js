"use strict";
/* =========================================================================
   Data loading: host manifest + snapshot fetches
   ========================================================================= */
const FALLBACK_HOSTS = [{ id: "hinton", label: "hinton", file: "hinton", default: true }];

function normalizeHostManifest(value) {
  if (!Array.isArray(value)) throw new Error("data/hosts.json must be an array");
  const hosts = value.map((h, idx) => {
    if (!h || typeof h !== "object") throw new Error("host entry " + idx + " must be an object");
    const id = String(h.id || "").trim();
    const label = String(h.label || id).trim();
    const file = String(h.file || id).trim();
    if (!/^[A-Za-z0-9._-]+$/.test(id)) throw new Error("unsafe host id: " + id);
    if (!/^[A-Za-z0-9._/-]+$/.test(file) || file.includes("..") || file.startsWith("/")) throw new Error("unsafe host file: " + file);
    if (!id || !label || !file) throw new Error("host entry " + idx + " needs id, label, and file");
    return { id, label, file, description: h.description || "", default: !!h.default };
  });
  if (!hosts.length) throw new Error("host manifest is empty");
  if (!hosts.some(h => h.default)) hosts[0].default = true;
  return hosts;
}

async function tryFetch(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

async function loadHostManifest() {
  try {
    const hosts = normalizeHostManifest(await tryFetch("data/hosts.json"));
    setUiWarning("manifest", null);
    return hosts;
  } catch (e) {
    console.warn("[storage-viz] using fallback host manifest", e);
    setUiWarning("manifest", "Host manifest data/hosts.json unavailable; using hinton-only development fallback.");
    return FALLBACK_HOSTS.slice();
  }
}

async function loadHost(host) {
  const realUrl = "data/" + host.file + ".json";
  const sampleUrl = "data/" + host.file + ".sample.json";
  try {
    const j = await tryFetch(realUrl);
    setUiWarning("data", null);
    console.info("[storage-viz] loaded", realUrl);
    return j;
  } catch (realErr) {
    try {
      const j = await tryFetch(sampleUrl);
      setUiWarning("data", "Loaded sample fixture " + sampleUrl + " because real snapshot " + realUrl + " is unavailable.");
      console.info("[storage-viz] loaded", sampleUrl);
      return j;
    } catch (sampleErr) {
      setUiWarning("data", "Missing snapshot for " + host.label + ": tried " + realUrl + " and " + sampleUrl + ".");
      throw sampleErr || realErr;
    }
  }
}

if (typeof module !== "undefined" && module.exports) module.exports = { normalizeHostManifest };
