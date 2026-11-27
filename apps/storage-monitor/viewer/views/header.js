import {humanBytes, escapeHtml} from '../format.js';
import {mountId} from '../state.js';
import {hasVolumeFolderLevel} from './overview.js';
export function renderHeader(DATA, selected, navigate) {
  document.getElementById("h-host").textContent = DATA.hostname || "—";

  const warn = document.getElementById("warnBanner");
  const msgs = [];
  for (const v of DATA.volumes) {
    if (v.diagnostic || v.last_attempt?.error_code) msgs.push(escapeHtml(v.mountpoint + ' · ' + (v.diagnostic || v.last_attempt.error_code)));
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
  const capacityGroups = DATA.volumes.map(v => ({
    path:v.mountpoint, media:v.media, fstype:'', usedBytes:v.capacity?.used, totalBytes:v.capacity?.total,
    availableBytes:v.capacity?.avail, usedPct:v.capacity?.used_pct, severity:v.severity,
    capacityObservedUnix:v.capacity?.observed_unix,
    scanRoots:actionableMounts.filter(m => m.volume_id === v.volume_id).map(m => ({path:m.path,mountPath:m.path,scannedBytes:m.scanned_bytes}))
  }));
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
      const scanSize = '<span class="figure">' + humanBytes(root.scannedBytes, 1000) + '</span>';
      button.innerHTML = pathLabel + scanSize + '<span aria-hidden="true">›</span>';
      button.addEventListener("click", () => {
        navigate({tab:"treemap", volumeId:mountId(actionableMounts[index]), path:group.scanRoots.length > 1 ? root.path.split("/").filter(Boolean) : []});
        document.getElementById("treemapFolderLabel").scrollIntoView({ block: "nearest" });
      });
      folders.appendChild(button);
    }
    div.appendChild(folders);
    caps.appendChild(div);
  }
  document.getElementById("treemapFolderLabel").textContent = selected ? selected.path + " · " + humanBytes(selected.scanned_bytes, 1000) : "";

}
