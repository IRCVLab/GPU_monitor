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

function mountRows(volumes) {
  return volumes.flatMap(volume => volume.roots.map(root => ({
    ...root, volume_id:volume.volume_id, path:root.scan_root, scanned_bytes:root.bytes, mountpoint:volume.mountpoint,
    status:volume.capture?.outcome ?? 'skipped',
  })));
}

export function normalizeDetail(body) {
  const mounts = mountRows(body.volumes);
  const names = new Map(body.users.map(user => [user.uid,user.name]));
  const file = row => ({...row,kind:row.kind === 'dir' ? 'directory' : row.kind ?? 'file',owner:names.get(row.uid)});
  return {...body, hostname:body.host.hostname, mounts, selected_roots:mounts,
    top_files:body.top_files.map(file), stale:body.stale.map(file)};
}

function treeNode(node) {
  return {...node, kind:node.kind === 'dir' ? 'directory' : node.kind,
    children:node.children?.map(treeNode)};
}

export async function fetchResource(key, signal, options = {}) {
  const [kind,id] = key.split('/');
  const server = '/api/v2/servers/' + encodeURIComponent(id);
  let path = server;
  if (kind === 'capture') path = server + '/volumes/' + encodeURIComponent(options.volumeId) + '/captures/' + options.sha;
  else if (kind === 'session' || kind === 'fleet') path = '/api/v2/' + kind;
  else if (kind !== 'detail') path += '/' + kind;
  const {sha, volumeId, ...init} = options;
  const body = await fetchJson(path,{...init, signal, cache:kind === 'capture' ? 'default' : 'no-store'});
  if (!body || typeof body !== 'object') throw new Error('Invalid '+kind+' response');
  if (kind === 'fleet' && !Array.isArray(body.servers)) throw new Error('Missing fleet rows');
  if (kind === 'detail') {
    if (body.server_id !== id || !Array.isArray(body.volumes)) throw new Error('Invalid server detail');
    return normalizeDetail(body);
  }
  if (kind === 'capture') {
    if (body.schema !== 'storage-viz.capture/2' || body.volume_id !== volumeId) throw new Error('Unexpected capture');
    return {...body,roots:body.roots.map(root => ({...root,tree:treeNode(root.tree)}))};
  }
  return body;
}
