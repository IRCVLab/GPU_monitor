function humanBytes(n, base = 1024) {
  if (n == null || isNaN(n)) return "—";
  if (n < base) return n + " B";
  const u = ["KB", "MB", "GB", "TB", "PB"];
  let i = -1, v = n;
  do { v /= base; i++; } while (v >= base && i < u.length - 1);
  return v.toFixed(v < 10 ? 2 : (v < 100 ? 1 : 0)) + " " + u[i];
}
function fmtDate(unix) {
  if (!unix) return "—";
  const d = new Date(unix * 1000);
  if (isNaN(d.getTime())) return "—";
  const p = (x) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}
function fmtAge(days) {
  if (days == null) return "—";
  if (days < 1) return "today";
  if (days < 30) return days + "d";
  if (days < 365) return (days / 30).toFixed(days < 90 ? 1 : 0) + "mo";
  return (days / 365).toFixed(1) + "y";
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function mountOf(path, mountPaths) {
  let best = "/";
  for (const mp of mountPaths) {
    if (mp === "/") continue;
    if ((path === mp || path.startsWith(mp + "/")) && mp.length > best.length) best = mp;
  }
  return best;
}

/* ---- Color: stable uid palette + fixed per-mount palette ----
   Harmonious, desaturated Apple-like categorical set: evenly-spaced hues at a
   uniform saturation/lightness (~S52% L56%) so owners read as one calm family
   inside the light card (and stay legible in dark), with clear hue separation. */
const PALETTE = [
  "#548fc9", "#5463c9", "#7254c9", "#9d54c9", "#c954c9",
  "#c9549d", "#c95472", "#c96354", "#c98f54", "#c9bb54",
  "#acc954", "#80c954", "#54c954", "#54c980", "#54c9ac",
  "#54bbc9"
];
const OTHER_COLOR = "#9aa0a6";

/* Pick the label color that maximizes WCAG contrast against the tile color —
   white vs near-black, whichever wins. (A fixed luminance cutoff mis-handles
   mid-tone tiles, e.g. our desaturated greens read far better with dark text.) */
function relLum(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255,
        g = parseInt(hex.slice(3, 5), 16) / 255,
        b = parseInt(hex.slice(5, 7), 16) / 255;
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
const DARK_LABEL = "#1d1d1f";
function labelColorForBg(hex) {
  if (!hex || hex[0] !== "#" || hex.length < 7) return "#ffffff";
  const L = relLum(hex), Ldark = relLum(DARK_LABEL);
  const cWhite = (1.0 + 0.05) / (L + 0.05);          // contrast vs white
  const cDark = (L + 0.05) / (Ldark + 0.05);         // contrast vs near-black
  return cWhite >= cDark ? "#ffffff" : DARK_LABEL;
}
/* per-mount colors: same calm family, hues spread for clear separation */
const MOUNT_PALETTE = ["#548fc9", "#9d54c9", "#54c954", "#c98f54", "#c9549d", "#54c9ac"];

export const clockSeconds = () => Date.now() / 1000;
export const monotonicSeconds = () => performance.now() / 1000;
export {humanBytes, fmtDate, fmtAge, escapeHtml, mountOf, PALETTE, OTHER_COLOR, labelColorForBg, MOUNT_PALETTE};
