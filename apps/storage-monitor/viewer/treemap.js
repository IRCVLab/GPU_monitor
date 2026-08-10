"use strict";

/* =========================================================================
   Treemap — build data ONLY for the selected mount, on demand
   ========================================================================= */
/* ---- HTML/CSS squarified treemap (real DOM tiles — NO canvas) ----
   Replaces the ECharts canvas treemap, which rendered tiles half-clipped in the
   user's browser. DOM tiles are always fully rendered and fit the container
   exactly, so there is no canvas to mis-size or clip. */
let treemapStack = [];   // drill path: [{node, name, mount}] from mount root to current
let treemapModifierActive = false;
let treemapCleanupLatched = false;

function rectArea(r) {
  return Math.max(0, Number(r && r.w) || 0) * Math.max(0, Number(r && r.h) || 0);
}

function squarify(items, x, y, w, h) {
  const nodes = items.filter(d => d.value > 0).sort((a, b) => b.value - a.value);
  const total = nodes.reduce((s, d) => s + d.value, 0);
  if (total <= 0 || w <= 0 || h <= 0) return [];
  const scale = (w * h) / total;
  nodes.forEach(d => { d._a = d.value * scale; });
  const worst = (row, side) => {
    let s = 0, mn = Infinity, mx = 0;
    for (const r of row) { s += r._a; if (r._a < mn) mn = r._a; if (r._a > mx) mx = r._a; }
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  let X = x, Y = y, W = w, H = h, i = 0;
  while (i < nodes.length) {
    const side = Math.min(W, H);
    const row = [nodes[i]]; let j = i + 1;
    while (j < nodes.length && worst(row.concat(nodes[j]), side) <= worst(row, side)) { row.push(nodes[j]); j++; }
    const rowArea = row.reduce((s, r) => s + r._a, 0);
    if (W >= H) { const cw = rowArea / H; let cy = Y; for (const r of row) { const ih = r._a / cw; r.x = X; r.y = cy; r.w = cw; r.h = ih; cy += ih; } X += cw; W -= cw; }
    else { const rh = rowArea / W; let cx = X; for (const r of row) { const iw = r._a / rh; r.x = cx; r.y = Y; r.w = iw; r.h = rh; cx += iw; } Y += rh; H -= rh; }
    i = j;
  }
  return nodes;
}

function renderMountSeg() {
  const wrap = document.getElementById("mountSeg"); wrap.innerHTML = "";
  (DATA.mounts || []).forEach((m, i) => {
    const b = document.createElement("button");
    b.className = (i === currentMountIdx ? "active" : ""); b.textContent = m.path;
    b.title = m.path + " · scanned " + humanBytes(m.scanned_bytes);
    b.onclick = () => { currentMountIdx = i; renderMountSeg(); renderTreemap(); };
    wrap.appendChild(b);
  });
}

/* Fit the treemap to the viewport: from the chart's top down to the bottom, minus
   the legend that sits below it. Sets an explicit px height and lets ECharts match
   its canvas to it (same approach as the verified debug page). A floor keeps it
   usable; the page can scroll on very short windows rather than clipping tiles. */
function sizeTreemap() {
  const main = document.getElementById("main");
  const el = document.getElementById("treemap");
  const toolbar = document.querySelector("#panel-treemap .toolbar");
  const legend = document.querySelector("#panel-treemap .legend");
  const cs = getComputedStyle(main);
  const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
  const toolbarH = toolbar ? toolbar.offsetHeight : 0;
  const legendH = legend ? legend.offsetHeight : 0;
  // Fit INSIDE the scroll container (main), not the raw viewport: toolbar + chart
  // and the dedicated legend rail must equal main's content height.
  const avail = main.clientHeight - pad - toolbarH - legendH - 12;
  const h = Math.max(300, Math.floor(avail));
  el.style.height = h + "px";
  return h;
}

function isDark() {
  const root = document.documentElement;
  if (root && root.classList && root.classList.contains("dark")) return true;
  if (root && root.classList && root.classList.contains("light")) return false;
  return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

const TM_MAXLEVEL = 3; // nest up to this many levels deep (drill for more); keeps tile area ∝ size
const TM_MIN_VISIBLE_SIDE = 1;      // below this, border pixels would visually exaggerate size
const TM_MIN_VISIBLE_AREA = 18;     // tiny labels/tiles are summarized instead of inflated
function tmChildren(node) {
  const kids = (node.children || []).map(c => ({ c, value: c.bytes })).filter(k => k.value > 0);
  if (node.other_bytes > 0) kids.push({ c: { name: "(other small files)", bytes: node.other_bytes, uid: node.uid, _other: true }, value: node.other_bytes });
  return kids;
}
function addHiddenTreemapItem(el, c) {
  if (!el._tmHiddenItems) el._tmHiddenItems = { bytes: 0, count: 0 };
  el._tmHiddenItems.bytes += c.bytes || 0;
  el._tmHiddenItems.count += 1;
}
function renderTreemapScaleNote(el) {
  const h = el._tmHiddenItems;
  if (!h || !h.count || h.bytes <= 0) return;
  const legend = document.getElementById("treemapLegend");
  if (!legend) return;
  const note = document.createElement("div");
  const itemLabel = h.count.toLocaleString() + " tiny item" + (h.count === 1 ? "" : "s");
  note.className = "legend-item tm-scale-note";
  note.textContent = itemLabel + " · " + humanBytes(h.bytes) + " hidden";
  note.setAttribute("title",
    "Tiny items too small to draw proportionally are hidden. Drill in or use tables for exact paths. / " +
    "실제 비율을 지키기 위해 너무 작은 항목은 숨깁니다. 자세한 경로는 드릴인하거나 표에서 확인하세요.");
  note.setAttribute("role", "note");
  note.setAttribute("tabindex", "0");
  legend.appendChild(note);
}
/* Progressively darken the owner color with nesting depth so the hierarchy is
   readable at a glance (top-level full color, each level deeper a bit darker). */
function shade(hex, level) {
  if (level <= 0 || hex[0] !== "#") return hex;
  const f = 1 - Math.min(0.40, level * 0.11);
  const v = i => Math.round(parseInt(hex.slice(i, i + 2), 16) * f).toString(16).padStart(2, "0");
  return "#" + v(1) + v(3) + v(5);
}
function treemapPath(crumbPath, name) {
  return (String(crumbPath || "") + "/" + String(name || "")).replace(/\/+/g, "/");
}
function pathDepth(path) {
  return String(path || "").split("/").filter(Boolean).length;
}
function isTopLevelCleanupPath(path) {
  const normalized = String(path || "").replace(/\/+$/g, "") || "/";
  return normalized === "/" || pathDepth(normalized) <= 1;
}
function treemapCleanupCandidate(path, kind, owner, bytes, source) {
  return {
    path,
    kind,
    owner,
    bytes: Number(bytes) || 0,
    source: source || "treemap",
  };
}
function treemapCleanupSnapshot() {
  if (typeof DATA !== "undefined" && DATA) return DATA;
  if (typeof globalThis !== "undefined" && globalThis.DATA) return globalThis.DATA;
  return null;
}
function isTreemapCleanupSelectablePath(path, kind, owner, bytes) {
  const snapshot = treemapCleanupSnapshot();
  if (typeof validateCleanupSelection !== "function" || !snapshot) return false;
  return validateCleanupSelection(snapshot, treemapCleanupCandidate(path, kind, owner, bytes)).accepted;
}
function isTreemapCleanupGesture(e) {
  return !!(treemapModifierActive || (e && (e.ctrlKey || e.metaKey)));
}
function treemapShortcutCopy(active, latched) {
  return active
    ? {
        label: "Selecting… / 선택 중",
        hint: latched
          ? "Select multiple tiles; click this button again to finish · 여러 타일 선택 후 버튼을 다시 눌러 종료"
          : "Release Ctrl/⌘ to leave selection mode · 키를 떼면 선택 모드 종료",
      }
    : {
        label: "Ctrl/⌘ click: select for removal",
        hint: "Click: drill · Ctrl/⌘ click: select for removal · 클릭: 열기 · Ctrl/⌘ 클릭: 삭제 선택",
      };
}
function treemapCleanupItem(c, path, owner) {
  return {
    path,
    kind: c && c.kind,
    bytes: Number(c && c.bytes) || 0,
    uid: c && c.uid,
    owner,
    source: "treemap",
  };
}
function treemapTileAriaLabel(path, hasKids, selectable) {
  if (hasKids && selectable) {
    return "Click or Enter drills into " + path + "; Ctrl/Command click or selection mode selects " + path + " for removal";
  }
  if (hasKids) return "Click or Enter drills into " + path;
  if (selectable) return "Select snapshot path " + path + " for removal";
  return "";
}
function syncTreemapTileSelection(tile) {
  if (!tile || !tile.dataset || !tile.dataset.cleanupPath) return;
  const selected = typeof isCleanupSelectedPath === "function" && isCleanupSelectedPath(tile.dataset.cleanupPath);
  tile.classList.toggle("cleanup-selected", selected);
  tile.setAttribute("aria-pressed", selected ? "true" : "false");
}
function syncTreemapCleanupTiles() {
  if (typeof document === "undefined") return;
  document.querySelectorAll(".tmtile[data-cleanup-path]").forEach(syncTreemapTileSelection);
}
function addTreemapCleanupMetadata(tile, c, path, owner) {
  if (c._other || !isTreemapCleanupSelectablePath(path, c && c.kind, owner, c && c.bytes)) return;
  tile.dataset.cleanupPath = path;
  tile.dataset.cleanupKind = c.kind || "";
  tile.dataset.cleanupBytes = String(Number(c.bytes) || 0);
  tile.dataset.cleanupOwner = owner || "";
  tile.dataset.cleanupSource = "treemap";
  tile.setAttribute("role", "button");
  tile.setAttribute("tabindex", "0");
  const badge = document.createElement("div");
  badge.className = "tm-cleanup-badge";
  badge.textContent = "✓";
  badge.setAttribute("aria-hidden", "true");
  tile.appendChild(badge);
  syncTreemapTileSelection(tile);
}
function toggleTreemapCleanupTile(tile, c, path, owner) {
  if (typeof toggleCleanupSelectedItem !== "function") return;
  toggleCleanupSelectedItem(treemapCleanupItem(c, path, owner));
  if (typeof renderCleanupPanel === "function") renderCleanupPanel();
  syncTreemapTileSelection(tile);
}
function setTreemapModifierActive(active) {
  treemapModifierActive = !!active;
  if (typeof document !== "undefined") {
    const el = document.getElementById("treemap");
    if (el) el.classList.toggle("cleanup-select-mode", treemapModifierActive);
    const btn = document.getElementById("treemapCleanupMode");
    if (btn) {
      const copy = treemapShortcutCopy(treemapModifierActive, treemapCleanupLatched);
      btn.classList.toggle("active", treemapModifierActive);
      btn.setAttribute("aria-pressed", treemapModifierActive ? "true" : "false");
      btn.textContent = copy.label;
      btn.title = copy.hint;
    }
    const hint = document.getElementById("treemapCleanupHint");
    if (hint) {
      hint.textContent = treemapShortcutCopy(treemapModifierActive, treemapCleanupLatched).hint;
    }
  }
  syncTreemapCleanupTiles();
  return treemapModifierActive;
}
function setTreemapCleanupMode(enabled) {
  treemapCleanupLatched = !!enabled;
  return setTreemapModifierActive(treemapCleanupLatched);
}
function bindTreemapCleanupMode() {
  if (typeof document === "undefined") return;
  const btn = document.getElementById("treemapCleanupMode");
  if (btn && !btn._cleanupModeBound) {
    btn._cleanupModeBound = true;
    btn.onclick = () => setTreemapCleanupMode(!treemapCleanupLatched);
  }
  if (!document._treemapCleanupShortcutBound) {
    document._treemapCleanupShortcutBound = true;
    document.addEventListener("keydown", e => {
      if (e.ctrlKey || e.metaKey || e.key === "Control" || e.key === "Meta" || e.key === "OS") {
        setTreemapModifierActive(true);
      }
    });
    document.addEventListener("keyup", e => {
      if ((e.key === "Control" || e.key === "Meta" || e.key === "OS") && !e.ctrlKey && !e.metaKey) {
        setTreemapModifierActive(treemapCleanupLatched);
      }
    });
    if (typeof window !== "undefined") window.addEventListener("blur", () => setTreemapModifierActive(treemapCleanupLatched));
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) setTreemapModifierActive(treemapCleanupLatched);
    });
  }
  if (!document._treemapCleanupRenderedBound) {
    document._treemapCleanupRenderedBound = true;
    document.addEventListener("cleanup-selection-rendered", syncTreemapCleanupTiles);
  }
  setTreemapModifierActive(false);
}
function activateTreemapTile(tile, c, path, owner, event) {
  if (event && typeof event.stopPropagation === "function") event.stopPropagation();
  if (isTreemapCleanupGesture(event)) {
    if (tile && c && path && owner != null && typeof toggleCleanupSelectedItem === "function") {
      toggleTreemapCleanupTile(tile, c, path, owner);
    }
    return;
  }
  if (c && c.children && c.children.length) {
    treemapStack.push({ node: c, name: c.name });
    renderTreemap();
  }
}
function tmTile(el, c, k, crumbPath, isGroup, level) {
  const base = c._other ? OTHER_COLOR : colorForUid(c.uid);
  const color = shade(base, level);
  const fg = labelColorForBg(color);
  const owner = ownerByUid.get(c.uid) || ("uid " + c.uid);
  const path = treemapPath(crumbPath, c.name);
  const hasKids = !c._other && c.children && c.children.length;
  const selectable = !c._other && isTreemapCleanupSelectablePath(path, c.kind, owner, c.bytes) && typeof toggleCleanupSelectedItem === "function";
  const t = document.createElement("div");
  t.className = "tmtile" + (isGroup ? " tmgroup" : "");
  t.style.zIndex = isGroup ? "1" : "2";
  t.style.left = k.x + "px"; t.style.top = k.y + "px";
  t.style.width = Math.max(0, k.w) + "px"; t.style.height = Math.max(0, k.h) + "px";
  t.style.background = color; if (c._other) t.style.opacity = ".55";
  t._tip = { path, bytes: c.bytes, owner, files: c.files, mtime: c.mtime, color, other: !!c._other, level };
  if (selectable) addTreemapCleanupMetadata(t, c, path, owner);
  if (k.w > 38 && k.h > 16) {
    const lab = document.createElement("div");
    lab.className = isGroup ? "tmhead" : "tmlabel";
    const lfg = isGroup ? "#ffffff" : fg;   // group titles sit on a dark gradient bar → always white
    lab.style.color = lfg;
    lab.style.textShadow = (lfg === "#ffffff" ? "0 1px 2px rgba(0,0,0,.6)" : "0 1px 2px rgba(255,255,255,.5)");
    lab.innerHTML = isGroup
      ? '<span class="tmgname">' + escapeHtml(c.name) + '</span> <span class="tmgsize">' + humanBytes(c.bytes) + '</span>'
      : '<div class="tmname">' + escapeHtml(c.name) + '</div><div class="tmsize">' + humanBytes(c.bytes) + '</div>';
    t.appendChild(lab);
  }
  if (hasKids) {
    t.classList.add("drillable");
  }
  if (selectable || hasKids) {
    t.setAttribute("role", "button");
    t.setAttribute("tabindex", "0");
    const ariaLabel = treemapTileAriaLabel(path, hasKids, selectable);
    if (ariaLabel) t.setAttribute("aria-label", ariaLabel);
    t.onclick = (e) => activateTreemapTile(t, c, path, owner, e);
    t.onkeydown = (e) => {
      if (!e || (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar")) return;
      if (typeof e.preventDefault === "function") e.preventDefault();
      activateTreemapTile(t, c, path, owner, e);
    };
  }
  el.appendChild(t);
}
/* Recursive nested layout: each parent group renders a header band + its own
   children squarified inside it, up to TM_DEPTH levels (drill for deeper). */
function layoutTreemap(el, node, x, y, w, h, level, crumbPath) {
  if (w < 8 || h < 8) return;
  const kids = tmChildren(node);
  if (!kids.length) return;
  const rects = squarify(kids, x, y, w, h);
  const GAP = level === 0 ? 3 : 2, HEAD = 20;
  for (const k of rects) {
    const c = k.c;
    const ix = k.x + GAP / 2, iy = k.y + GAP / 2, iw = Math.max(0, k.w - GAP), ih = Math.max(0, k.h - GAP);
    if ((k.w * k.h) < TM_MIN_VISIBLE_AREA || iw < TM_MIN_VISIBLE_SIDE || ih < TM_MIN_VISIBLE_SIDE) {
      addHiddenTreemapItem(el, c);
      continue;
    }
    const hasKids = !c._other && c.children && c.children.length;
    // Cap nesting at TM_MAXLEVEL: each nested header steals area from its children,
    // so unbounded depth makes deep folders render far smaller than their size.
    const nest = level < TM_MAXLEVEL && hasKids && iw > 110 && ih > 84;
    tmTile(el, c, { x: ix, y: iy, w: iw, h: ih }, crumbPath, nest, level);
    if (nest) layoutTreemap(el, c, ix + 4, iy + HEAD, iw - 8, ih - HEAD - 4, level + 1, crumbPath + "/" + c.name);
  }
}
function renderTreemap() {
  if (!DATA) return;
  const m = (DATA.mounts || [])[currentMountIdx];
  const el = document.getElementById("treemap");
  if (!m || !m.tree) { el.innerHTML = '<div class="empty">No tree data for this mount.</div>'; return; }
  if (!treemapStack.length || treemapStack[0].mount !== m.path || treemapStack[0].snapshot !== DATA) {
    treemapStack = [{ node: m.tree, name: m.path, mount: m.path, snapshot: DATA }];
  }
  renderTreemapLegend();
  const H = sizeTreemap(), W = el.clientWidth;
  el.innerHTML = "";
  el._tmHiddenItems = { bytes: 0, count: 0 };

  const bc = document.createElement("div"); bc.className = "tm-bc";
  treemapStack.forEach((s, idx) => {
    const b = document.createElement("button"); b.className = "tm-crumb"; b.textContent = s.name;
    b.onclick = () => { treemapStack = treemapStack.slice(0, idx + 1); renderTreemap(); };
    bc.appendChild(b);
    if (idx < treemapStack.length - 1) { const sep = document.createElement("span"); sep.className = "tm-sep"; sep.textContent = "›"; bc.appendChild(sep); }
  });
  el.appendChild(bc);

  const cur = treemapStack[treemapStack.length - 1].node;
  const crumbPath = treemapStack.map(s => s.name).join("/").replace(/\/+/g, "/");
  if (!tmChildren(cur).length) { const d = document.createElement("div"); d.className = "tm-note"; d.textContent = "No sub-items above the size threshold here."; el.appendChild(d); return; }
  const P = 10, TOP = 38;
  layoutTreemap(el, cur, P, TOP, Math.max(0, W - 2 * P), Math.max(0, H - TOP - P), 0, crumbPath);
  setTreemapModifierActive(treemapModifierActive);
  renderTreemapScaleNote(el);
}

function renderTreemapLegend() {
  const wrap = document.getElementById("treemapLegend"); wrap.innerHTML = "";
  const seen = new Set();
  for (const [uid, color] of uidColorMap) {
    if (color === OTHER_COLOR || seen.has(color)) continue; seen.add(color);
    const it = document.createElement("div"); it.className = "legend-item";
    it.innerHTML = '<span class="swatch" style="background:' + color + '"></span>' + escapeHtml(ownerByUid.get(uid) || ("uid " + uid));
    wrap.appendChild(it);
  }
  const o = document.createElement("div"); o.className = "legend-item";
  o.innerHTML = '<span class="swatch" style="background:' + OTHER_COLOR + '"></span>other owners / small-file remainder';
  wrap.appendChild(o);
}


if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    rectArea,
    squarify,
    tmChildren,
    layoutTreemap,
    renderTreemapScaleNote,
    setTreemapCleanupMode,
    setTreemapModifierActive,
    isTreemapCleanupGesture,
    isTopLevelCleanupPath,
    isTreemapCleanupSelectablePath,
    bindTreemapCleanupMode,
    TM_MAXLEVEL,
    TM_MIN_VISIBLE_SIDE,
    TM_MIN_VISIBLE_AREA,
  };
}
