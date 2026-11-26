import {humanBytes,escapeHtml} from '../format.js';
import {columnValue,sortRows,updateArrows} from '../columns.js';
export function drawUsers({DATA,route,mountPaths,mountColor,colorForUid,navigate,sort:usersSort,ui,toggleUserRow},echarts) {
  const userMountFilter = route.mount || "";
  const userScopeBytes = user => columnValue("users","bytes",user,route);
  const scopeCapacity = () => DATA.mounts.filter(m => !route.mount || m.path === route.mount)
    .reduce((sum,m) => sum + (m.scanned_bytes ?? 0),0) || 1;
  const expandedUid = ui.expandedUsers?.[route.serverId];
  function renderUserMountControls() {
    const seg = document.getElementById("userMountSeg"); seg.innerHTML = "";
    [["", "All"]].concat(mountPaths.map(p => [p, p])).forEach(([val, lab]) => {
      const b = document.createElement("button");
      b.className = (userMountFilter === val ? "active" : ""); b.textContent = lab;
      b.onclick = () => { navigate({mount:val || null}); };
      seg.appendChild(b);
    });
    const leg = document.getElementById("userMountLegend"); leg.innerHTML = "";
    (userMountFilter ? [userMountFilter] : mountPaths).forEach(mp => {
      const it = document.createElement("div"); it.className = "legend-item";
      it.innerHTML = '<span class="swatch" style="background:' + mountColor[mp] + '"></span><span class="mono">' + escapeHtml(mp) + '</span>';
      leg.appendChild(it);
    });
  }

  function renderUsers() {
    const el = document.getElementById("usersChart");
    const dark = ui.theme === "dark";
    const axisText = dark ? "#98989d" : "#6e6e73", labelText = dark ? "#f5f5f7" : "#1d1d1f", split = dark ? "#2c2c2e" : "#e8e8ed";

    const ranked = [...(DATA.users || [])].map(u => ({ u, scope: userScopeBytes(u) }))
      .filter(x => x.scope > 0).sort((a, b) => b.scope - a.scope);
    const top = ranked.slice(0, 20);
    const names = top.map(x => x.u.name || ("uid " + x.u.uid)).reverse();
    el.style.height = Math.max(320, top.length * 25 + 70) + "px";
    const usersChart = echarts.getInstanceByDom(el) || echarts.init(el, null, {renderer:"canvas"});
    usersChart.resize();

    const mountsShown = userMountFilter ? [userMountFilter] : mountPaths;
    const series = mountsShown.map(mp => ({
      name: mp, type: "bar", stack: "u", barMaxWidth: 17,
      itemStyle: { color: mountColor[mp] }, emphasis: { focus: "series" },
      data: top.map(x => (x.u.by_mount && x.u.by_mount[mp]) || 0).reverse()
    }));
    if (series.length) series[series.length - 1].label = {
      show: true, position: "right", color: axisText, fontFamily: "ui-monospace, monospace", fontSize: 11,
      formatter: (p) => humanBytes(top[top.length - 1 - p.dataIndex].scope)
    };

    usersChart.setOption({
      grid: { left: 6, right: 100, top: 8, bottom: 24, containLabel: true },
      tooltip: {
        trigger: "axis", axisPointer: { type: "shadow" },
        backgroundColor: dark ? "#2c2c2e" : "#fff", borderColor: dark ? "#48484a" : "#d2d2d7", borderWidth: 1,
        textStyle: { color: labelText, fontFamily: "ui-monospace, monospace", fontSize: 12 },
        extraCssText: "border-radius:10px",
        formatter: (params) => {
          const idx = params[0].dataIndex, x = top[top.length - 1 - idx], cap = scopeCapacity();
          let s = '<b>' + escapeHtml(x.u.name) + '</b>  <span style="opacity:.6">uid ' + x.u.uid + '</span><br>';
          s += humanBytes(x.scope) + '  ·  ' + (x.scope / cap * 100).toFixed(1) + '% of scanned usage' +
            (userMountFilter ? ' on ' + escapeHtml(userMountFilter) : ' across all storage') + '<br>';
          for (const p of params) if (p.value > 0) s += '<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:' +
            p.color + ';margin-right:6px"></span>' + p.seriesName + ' ' + humanBytes(p.value) + '<br>';
          return s;
        }
      },
      xAxis: { type: "value", axisLabel: { color: axisText, fontFamily: "ui-monospace, monospace", formatter: humanBytes },
               splitLine: { lineStyle: { color: split } }, axisLine: { show: false }, axisTick: { show: false } },
      yAxis: { type: "category", data: names, axisLabel: { color: labelText, fontFamily: "ui-monospace, monospace", fontSize: 12 },
               axisLine: { show: false }, axisTick: { show: false } },
      series
    }, true);

    usersChart.off("click");
    usersChart.on("click", (p) => { const x = top[top.length - 1 - p.dataIndex]; if (x) switchToUsersRow(x.u.uid); });

    renderUsersTable(); renderUserMountControls();
  }

  function switchToUsersRow(uid) {
    const row = document.querySelector('#usersTbl tbody tr[data-uid="' + uid + '"]');
    if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
    toggleUserRow(uid);
  }

  function renderUsersTable() {
    const tbody = document.querySelector("#usersTbl tbody"); tbody.innerHTML = "";
    const cap = scopeCapacity();
    const users = sortRows("users",DATA.users,usersSort,route)
      .map(u => ({u, scope:userScopeBytes(u)})).filter(entry => entry.scope > 0);
    updateArrows("#usersTbl", usersSort);
    document.getElementById("usersCount").textContent =
      users.length + " user" + (users.length === 1 ? "" : "s") + (userMountFilter ? " on " + userMountFilter : " across all storage");
    if (!users.length) { tbody.innerHTML = '<tr><td colspan="4" class="empty">No user data.</td></tr>'; return; }
    const frag = document.createDocumentFragment();
    for (const { u, scope } of users) {
      const pct = scope / cap * 100;
      const tr = document.createElement("tr");
      tr.dataset.uid = u.uid; tr.style.cursor = "pointer"; tr.tabIndex = 0;
      tr.innerHTML =
        '<td><span class="owner-chip"><span class="owner-dot" style="background:' + colorForUid(u.uid) + '"></span>' +
          escapeHtml(u.name || ("uid " + u.uid)) + ' <span class="hint">uid ' + u.uid + '</span></span></td>' +
        '<td class="num">' + humanBytes(scope) + '</td>' +
        '<td class="num">' + pct.toFixed(1) + '%</td>' +
        '<td class="num">' + (columnValue("users","files",u,route)?.toLocaleString() ?? "—") + '</td>';
      tr.onclick = () => toggleUserRow(u.uid);
      tr.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleUserRow(u.uid); } };
      frag.appendChild(tr);
      if (expandedUid === u.uid) frag.appendChild(expandedRow(u));
    }
    tbody.appendChild(frag);
  }

  function expandedRow(u) {
    const byMount = u.by_mount || {};
    const entries = mountPaths.map(mp => [mp, byMount[mp] || 0]).filter(e => e[1] > 0).sort((a, b) => b[1] - a[1]);
    let inner = '<div class="expand-inner">';
    if (!entries.length) inner += '<span class="hint">No per-storage breakdown.</span>';
    else for (const [mp, b] of entries) inner += '<div class="mini-bar"><span class="mb-label">' + escapeHtml(mp) +
      '</span><span class="mb-track"><span class="mb-fill" style="width:' + (b / u.bytes * 100).toFixed(2) + '%;background:' +
      mountColor[mp] + '"></span></span><span class="mb-val">' + humanBytes(b) + '</span><span class="mb-pct">' +
      (b / u.bytes * 100).toFixed(0) + '%</span></div>';
    inner += '</div>';
    const er = document.createElement("tr"); er.className = "expand-row"; er.dataset.for = u.uid;
    er.innerHTML = '<td colspan="4">' + inner + '</td>';
    return er;
  }


  renderUsers();
}
