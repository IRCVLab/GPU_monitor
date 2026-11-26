import {humanBytes, escapeHtml} from '../format.js';
import {mountId} from '../state.js';
import {compatibleCapacityMetrics, hasVolumeFolderLevel} from './overview.js';
function detailCapacityNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}
function detailSelectedRootByMountId(DATA) {
  const byMountId = new Map();
  const roots = Array.isArray(DATA && DATA.selected_roots) ? DATA.selected_roots : [];
  for (const root of roots) {
    const mountId = root && root.mount_id != null ? String(root.mount_id) : "";
    if (!mountId || byMountId.has(mountId)) continue;
    byMountId.set(mountId, root);
  }
  return byMountId;
}
function detailIsCanonicalDecimal(raw, allowZero) {
  if (!/^\d{1,10}$/.test(raw)) return false;
  if (raw.length > 1 && raw[0] === "0") return false;
  if (!allowZero && raw === "0") return false;
  return true;
}
function detailCanonicalCapacityId(value) {
  if (typeof value !== "string") return null;
  const match = value.match(/^dev-(\d+)-(\d+)$/);
  if (!match) return null;
  if (!detailIsCanonicalDecimal(match[1], false) || !detailIsCanonicalDecimal(match[2], true)) return null;
  return value;
}
function detailCanonicalMajorMinor(value) {
  if (typeof value !== "string") return null;
  const match = value.trim().match(/^(\d+):(\d+)$/);
  if (!match) return null;
  const major = Number(match[1]);
  const minor = Number(match[2]);
  if (!Number.isSafeInteger(major) || !Number.isSafeInteger(minor)) return null;
  if (major === 0 && minor === 0) return null;
  return String(major) + ":" + String(minor);
}
function detailCapacityIdentity(root, mount) {
  // A v2 host names its filesystem by volume id (SHIM(M5a..M8) S5), so its roots share one capacity entry.
  const volumeId = String((root && root.volume_id) || (mount && mount.volume_id) || "");
  if (/^[vx]-[0-9a-f]{16}$/.test(volumeId)) return { key: "volume_id:" + volumeId, value: volumeId };
  const capacityId = detailCanonicalCapacityId(
    String((root && root.capacity_id) || (mount && mount.capacity_id) || "")
  );
  if (capacityId) return { key: "capacity_id:" + capacityId, value: capacityId };
  const majorMinor = detailCanonicalMajorMinor(
    String((root && root.major_minor) || (mount && mount.major_minor) || "")
  );
  return majorMinor ? { key: "major_minor:" + majorMinor, value: majorMinor } : null;
}
function detailScanRootPath(mount, root) {
  return String(
    (root && (root.scan_root || root.path || root.mountpoint))
    || (mount && (mount.scan_root || mount.path || mount.mountpoint))
    || "/"
  );
}
function detailScannedBytes(mount, root) {
  const mountBytes = detailCapacityNumber(mount && mount.scanned_bytes);
  if (mountBytes != null) return mountBytes;
  return detailCapacityNumber(root && root.scanned_bytes);
}
function detailCapacityMetricsKnown(candidate) {
  return candidate
    && candidate.totalBytes != null
    && candidate.usedBytes != null
    && candidate.availableBytes != null
    && candidate.capacityPct != null;
}
function groupDetailCapacityMounts(mounts, DATA) {
  const rootsByMountId = detailSelectedRootByMountId(DATA);
  const groups = [];
  const groupsByIdentity = new Map();
  for (const mount of Array.isArray(mounts) ? mounts : []) {
    if (!mount) continue;
    const mountId = mount.mount_id != null ? String(mount.mount_id) : "";
    const root = mountId ? rootsByMountId.get(mountId) || null : null;
    const usedBytes = detailCapacityNumber(mount.df_used);
    const totalBytes = detailCapacityNumber(mount.df_total);
    const availableBytes = detailCapacityNumber(mount.df_avail);
    const usedPct = Number.isInteger(mount.used_pct) ? mount.used_pct : null;
    const capacityPct = Number.isFinite(mount.df_use_pct) ? usedPct : null;
    const identity = detailCapacityIdentity(root, mount);
    const identityKey = identity && identity.key ? identity.key : "";
    const existing = identityKey ? groupsByIdentity.get(identityKey) || null : null;
    const candidate = {
      key: identityKey || ("path:" + String(mount.path || mountId || groups.length)),
      path: String((root && root.mountpoint) || mount.mountpoint || mount.path || "/"),
      usedBytes,
      totalBytes,
      availableBytes,
      capacityPct,
      usedPct,
      severity: mount.severity || "unknown",
      capacityObservedUnix: mount.capacity_observed_unix,
      media: String((mount.storage_media || mount.block_media || (root && (root.storage_media || root.block_media)) || "unknown")),
      fstype: String((mount.fstype || (root && root.fstype) || "")),
      scanRoots: [{
        path: detailScanRootPath(mount, root),
        mountPath: String(mount.path || mount.mountpoint || "/"),
        scannedBytes: detailScannedBytes(mount, root),
      }],
    };
    const canMerge = !!(
      existing
      && detailCapacityMetricsKnown(existing)
      && detailCapacityMetricsKnown(candidate)
      && compatibleCapacityMetrics(existing, candidate)
      && existing.media === candidate.media
      && existing.fstype === candidate.fstype
    );
    if (!canMerge) {
      groups.push(candidate);
      if (identityKey) groupsByIdentity.set(identityKey, candidate);
      continue;
    }
    existing.scanRoots.push(candidate.scanRoots[0]);
  }
  return groups;
}
function isTransientBlockedEntry(entry) {
  const reason = String(entry && entry.reason || '').trim().toLowerCase();
  return reason === 'enoent' || reason === 'no such file or directory';
}
export function renderHeader(DATA, selected, navigate) {
  document.getElementById("h-host").textContent = DATA.hostname || "—";

  const warn = document.getElementById("warnBanner");
  const msgs = [];
  if (DATA.run_as_root === false) msgs.push("run_as_root=false → some directories were not scanned and are hidden from totals.");
  const blocked = (DATA.blocked || []).filter(entry => !isTransientBlockedEntry(entry));
  const nB = blocked.length;
  if (nB > 0) {
    const list = blocked.slice(0, 6).map(b => b.path + " (" + b.reason + ")").join(", ");
    msgs.push(nB + " director" + (nB === 1 ? "y" : "ies") + " could not be read: " + escapeHtml(list) + (nB > 6 ? " …" : ""));
  }
  warn.classList.toggle("show", msgs.length > 0);
  warn.innerHTML = msgs.length ? "⚠ " + msgs.join("<br>⚠ ") : "";

  const caps = document.getElementById("caps");
  const actionableMounts = DATA.mounts;
  caps.innerHTML = "";
  caps.className = "caps detail-capacity-rail";
  if (!actionableMounts.length) {
    const empty = document.createElement("div");
    empty.className = "detail-capacity-empty";
    empty.textContent = "표시할 데이터 마운트 없음";
    caps.appendChild(empty);
  }
  const capacityGroups = groupDetailCapacityMounts(actionableMounts, DATA);
  for (const group of capacityGroups) {
    const usedBytes = group.usedBytes;
    const totalBytes = group.totalBytes;
    const availableBytes = group.availableBytes;
    const pct = group.usedPct;
    const pressure = group.severity;
    const color = { critical: "var(--crit)", warning: "var(--warn)", normal: "var(--ok)" }[pressure] || null;
    const fillStyle = pct == null ? "width:0%" : "width:" + Math.min(100, pct) + "%" + (color ? ";background:" + color : "");
    const volumeTitle = hasVolumeFolderLevel(group.path, group.scanRoots) ? "볼륨 " + group.path : "볼륨";
    const volumeMedia = '<span class="cap-fs">' + escapeHtml(group.fstype) + '</span>' +
      '<span class="cap-media">' + escapeHtml(group.media) + '</span>';
    const div = document.createElement("div");
    div.className = "detail-capacity-row";
    div.setAttribute("data-pressure", pressure);
    div.title = group.capacityObservedUnix
      ? "볼륨 측정 " + new Date(group.capacityObservedUnix * 1000).toLocaleString()
      : "스캔 당시 볼륨 용량";
    div.innerHTML =
      '<div class="cap-main"><span class="cap-path">' + escapeHtml(volumeTitle) + '</span>' + volumeMedia + '</div>' +
      '<div class="cap-sub">볼륨 사용량 <span class="figure">' + humanBytes(usedBytes) + '</span> / <span class="figure">' +
        humanBytes(totalBytes) + '</span></div>' +
      '<div class="cap-pct"' + (color ? ' style="color:' + color + '"' : '') + '>' + (pct == null ? "—" : pct + '<span>%</span>') + '</div>' +
      '<div class="cap-free"><span class="figure">' + humanBytes(availableBytes) + '</span> 여유</div>' +
      '<div class="cap-bar"><div class="cap-fill" style="' + fillStyle + '"></div></div>';
    const folders = document.createElement("div");
    folders.className = "cap-scan-roots";
    folders.setAttribute("role", "group");
    folders.setAttribute("aria-label", "폴더 스캔");
    for (const root of group.scanRoots) {
      const index = actionableMounts.findIndex(mount => mount.path === root.mountPath);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "cap-folder-button";
      button.dataset.folder = root.path;
      button.setAttribute("aria-pressed", selected && mountId(actionableMounts[index]) === mountId(selected) ? "true" : "false");
      const pathLabel = '<span class="cap-path cap-scan-path">' + escapeHtml(root.path) + '</span>';
      const scanSize = '<span class="figure">' + humanBytes(root.scannedBytes) + '</span>';
      button.innerHTML = pathLabel + scanSize + '<span aria-hidden="true">›</span>';
      button.addEventListener("click", () => {
        navigate({tab:"treemap", volumeId:mountId(actionableMounts[index]), path:[]});
        document.getElementById("treemapFolderLabel").scrollIntoView({ block: "nearest" });
      });
      folders.appendChild(button);
    }
    div.appendChild(folders);
    caps.appendChild(div);
  }
  document.getElementById("treemapFolderLabel").textContent = selected ? selected.path + " · " + humanBytes(selected.scanned_bytes) : "";

}
