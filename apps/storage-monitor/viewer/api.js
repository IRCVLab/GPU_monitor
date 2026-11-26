function makeFetchError(url, status, body) {
  const err = new Error(url + " -> " + status);
  err.url = url;
  err.status = status;
  err.body = body;
  return err;
}
async function fetchJson(url, options) {
  const response = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
  const body = response.ok ? await response.json() : await response.json().catch(() => ({}));
  if (!response.ok) throw makeFetchError(url, response.status, body);
  return body;
}
function normalizeServerSummariesEnvelope(body) {
  const servers = Array.isArray(body) ? body : (Array.isArray(body && body.servers) ? body.servers : []);
  const dataMode = body && body.data_mode === "sample" ? "sample" : "inventory";
  return { data_mode: dataMode, servers };
}
const LIVE_CAPACITY_FIELDS = ["df_total", "df_used", "df_avail", "df_use_pct", "used_pct", "severity", "capacity_observed_unix"];

function applyObservedCapacity(snapshot, capacity) {
  if (!Array.isArray(snapshot?.mounts) || !Array.isArray(capacity?.mounts) ||
      snapshot.scan_generation !== capacity.scan_generation) return snapshot;
  const readings = new Map(capacity.mounts.map(mount => [mount.mount_id, mount]));
  let changed = false;
  const mounts = snapshot.mounts.map(mount => {
    const reading = readings.get(mount.mount_id);
    if (!reading || LIVE_CAPACITY_FIELDS.every(key => (mount[key] ?? null) === (reading[key] ?? null))) return mount;
    changed = true;
    return Object.assign({}, mount, Object.fromEntries(LIVE_CAPACITY_FIELDS.map(key => [key, reading[key] ?? null])));
  });
  return changed ? { ...snapshot, mounts } : snapshot;
}

// Keep raw route segments unambiguous even when a literal name starts with "~".
export function rawSegment(node) {
  if (node.raw_name != null) return node.raw_name;
  if (node.name_b64) return '~b64~' + node.name_b64.replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
  return node.name.startsWith('~') ? '~' + node.name : node.name;
}
export function pathRoundTrips(path, bytes) {
  if (!bytes) return true;
  try {
    const encoded = new TextEncoder().encode(path);
    const raw = atob(bytes);
    return raw.length === encoded.length && encoded.every((value,i) => raw.charCodeAt(i) === value);
  } catch { return false; }
}

function normalizedDetailPath(path) {
  return String(path || "").replace(/\/+$/, "") || "/";
}
function isDetailActionablePath(path) {
  const value = normalizedDetailPath(path);
  return value !== "/boot" && !value.startsWith("/boot/");
}
function isDetailActionableMount(mount) {
  return isDetailActionablePath(mount && (mount.path || mount.mountpoint));
}
function normalizeDetailFiles(files) {
  return (Array.isArray(files) ? files : []).filter(file => isDetailActionablePath(file && file.path));
}
function normalizeDetailUsers(users) {
  const rows = [];
  for (const user of Array.isArray(users) ? users : []) {
    const byMount = {};
    let bytes = 0;
    const sourceByMount = user && user.by_mount && typeof user.by_mount === "object" ? user.by_mount : {};
    for (const [mount, value] of Object.entries(sourceByMount)) {
      if (!isDetailActionablePath(mount)) continue;
      const n = Number(value) || 0;
      if (n <= 0) continue;
      byMount[mount] = n;
      bytes += n;
    }
    if (bytes <= 0) continue;
    rows.push(Object.assign({}, user, { by_mount: byMount, bytes }));
  }
  return rows;
}

const DEFAULT_HOSTS = [
  { id: "hinton", label: "hinton", file: "hinton", default: true }
];
function isSafeHostToken(value) {
  return typeof value === "string" && /^[A-Za-z0-9._-]+$/.test(value) && value !== "." && value !== "..";
}
function normalizeHosts(input) {
  const rows = Array.isArray(input) ? input : [];
  const out = [];
  for (const row of rows) {
    const id = row && String(row.id || "").trim();
    const file = row && String(row.file || id).trim();
    if (!isSafeHostToken(id) || !isSafeHostToken(file)) continue;
    const normalized = {
      id,
      label: String(row.label || id),
      file,
      default: row.default === true,
      description: row.description ? String(row.description) : "",
    };
    if (row.sample_data === true) normalized.sample_data = true;
    out.push(normalized);
  }
  if (!out.length) return DEFAULT_HOSTS.slice();
  return out;
}

async function staticFleet(signal) {
  const hosts = normalizeHosts(await fetchJson('data/hosts.json',{signal}).catch(error => {
    if (signal.aborted) throw error;
    return [];
  }));
  return {data_mode:hosts.every(host => host.sample_data) ? 'sample' : 'inventory',
    servers:hosts.map(({id,label,file},order) => ({id, file, order, display_name:label, snapshot_availability:'available',
      freshness:'unknown', latest_pull_status:'succeeded',latest_scan_result:'complete',configuration_sync:'in_sync'}))};
}

async function staticDetail(file,signal) {
  if (!isSafeHostToken(file)) throw makeFetchError('',404,{error:'UNKNOWN_SERVER'});
  try { return await fetchJson('data/' + file + '.json',{signal}); }
  catch (error) {
    if (signal.aborted) throw error;
    return fetchJson('data/' + file + '.sample.json',{signal});
  }
}

export async function fetchResource(key, signal, options = {}) {
  const [kind,id] = key.split('/');
  const routes = {detail:'snapshot',activity:'scan-status',job:'job',rescan:'rescan'};
  const path = kind === 'session' ? '/api/session' : kind === 'fleet' ? '/api/servers'
    : '/api/servers/' + encodeURIComponent(id) + '/' + routes[kind];
  const {source,file,...init} = options;
  if (kind === 'fleet' && source === 'static') return staticFleet(signal);
  let body;
  try {
    body = kind === 'detail' && source === 'static' ? await staticDetail(file,signal) : await fetchJson(path,{...init,signal});
  } catch (error) {
    if (kind === 'session' && error.status === 404) return {source:'static',authenticated:false,can_rescan:false};
    throw error;
  }
  if (kind === 'fleet') return normalizeServerSummariesEnvelope(body);
  if (kind !== 'detail') return body;
  return normalizeDetail(body);
}
export function normalizeDetail(body) {
  return {...body, mounts:(body.mounts || []).filter(isDetailActionableMount), users:normalizeDetailUsers(body.users),
    stale:normalizeDetailFiles(body.stale), top_files:normalizeDetailFiles(body.top_files)};
}
export {applyObservedCapacity};
