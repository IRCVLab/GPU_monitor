export function columnValue(table, key, row, {mount} = {}) {
  if (table === 'users') {
    if (key === 'files' && mount) return null;
    if (key === 'pct' || key === 'bytes') return mount ? row.by_mount?.[mount] || 0 : row.bytes;
    if (key === 'name') return row.name ?? ('uid ' + row.uid);
  }
  if (key === 'age' || key === 'age_days') return row.age_days ?? null;
  return row[key] ?? null;
}
export function sortRows(table, rows, {key,dir}, context) {
  return [...rows].sort((left,right) => {
    const a = columnValue(table,key,left,context), b = columnValue(table,key,right,context);
    if (a == null && b == null && table === 'users')
      return columnValue(table,'bytes',right,context) - columnValue(table,'bytes',left,context);
    if (a == null || b == null) return Number(a == null) - Number(b == null);
    return dir * (typeof a === 'string' ? a.localeCompare(b) : a - b);
  });
}

export function updateArrows(sel, sort) {
  document.querySelectorAll(sel + " th.sortable, " + sel + " .vc.sortable").forEach(th => {
    const a = th.querySelector(".arrow"); if (a) a.textContent = (th.dataset.key === sort.key) ? (sort.dir < 0 ? "▼" : "▲") : "";
  });
}
