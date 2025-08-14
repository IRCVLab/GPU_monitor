# app.py

import streamlit as st

# 반드시 가장 첫 줄에 한 번만!
st.set_page_config(page_title="GPU Dashboard", layout="wide")

from streamlit_autorefresh import st_autorefresh
import pandas as pd
import requests

# 자동 새로고침 (5초)
st_autorefresh(interval=5000, key="refresh")

st.title("🖥️ GPU 서버 모니터링 대시보드")
API_BASE = "http://localhost:5001"

# --- CSS Styles ---
st.markdown("""
<style>
    .status-dot {
        height: 10px;
        width: 10px;
        border-radius: 50%;
        display: inline-block;
        margin-right: 8px;
    }
    .status-ok {
        background-color: #28a745; /* Green */
    }
    .status-fail {
        background-color: #dc3545; /* Red */
    }
    .server-title {
        display: flex;
        align-items: center;
    }
    .styled-table {
        border-collapse: collapse;
        width: 100%;
        margin: auto;
    }
    .styled-table th, .styled-table td {
        padding: 8px;
        text-align: left;
        border-bottom: 1px solid #ddd;
    }
    .bar-container {
        position: relative;
        width: 100%;
        height: 20px;
        background-color: #f5f5f5;
        border-radius: 3px;
    }
    .bar {
        position: absolute;
        height: 100%;
        border-radius: 3px;
    }
    .bar-text {
        position: absolute;
        width: 100%;
        text-align: center;
        color: #333;
        z-index: 2;
        line-height: 20px;
        font-size: 0.9em;
    }
    .connection-error {
        color: #dc3545;
        font-weight: bold;
        padding: 20px;
        text-align: center;
        background-color: #f8d7da;
        border: 1px solid #f5c6cb;
        border-radius: 5px;
    }
</style>
""", unsafe_allow_html=True)


# 전체 서버 상태 한 번에 가져오기
try:
    resp = requests.get(f"{API_BASE}/stats", timeout=3)
    resp.raise_for_status()
    all_stats = resp.json()
except Exception as e:
    st.error(f"모니터링 서버에 연결할 수 없습니다: {e}")
    st.stop()

aliases = sorted(list(all_stats.keys()))

# 2×2 그리드로
for i in range(0, len(aliases), 2):
    cols = st.columns(2)
    for alias, col in zip(aliases[i:i+2], cols):
        data = all_stats.get(alias)
        with col:
            # --- Server Title with Status Dot ---
            status_class = "status-ok" if data else "status-fail"
            st.markdown(
                f'<div class="server-title"><span class="status-dot {status_class}"></span><h3>{alias[2:]}</h3></div>',
                unsafe_allow_html=True
            )

            if st.button("⟳ Reload", key=f"reload_{alias}"):
                try:
                    r = requests.post(f"{API_BASE}/reload/{alias}", timeout=3)
                    r.raise_for_status()
                    st.toast("갱신 요청 완료", icon="✅")
                except Exception as e:
                    st.error(f"재갱신 실패: {e}")

            if not data:
                st.markdown('<div class="connection-error">서버 연결 실패</div>', unsafe_allow_html=True)
                continue

            # 1) 원본 데이터에서 필요한 값 뽑아 리스트 생성
            rows = []
            for gpu in data.get("gpus", []):
                used = gpu.get("memory.used", 0)
                total = gpu.get("memory.total", 1) # Avoid division by zero
                pct = (used / total) * 100 if total > 0 else 0
                util = gpu.get("utilization.gpu", gpu.get("utilization", 0))
                users = ", ".join({p["username"] for p in gpu.get("processes", [])}) or "-"

                rows.append({
                    "GPU": str(gpu.get("index", "N/A")),
                    "memory_used": used,
                    "memory_total": total,
                    "memory_pct": round(pct, 1),
                    "util_pct": round(util, 1),
                    "Users": users,
                    "Temp °C": round(gpu.get("temperature.gpu", 0), 1),
                })

            df = pd.DataFrame(rows)

            # --- Custom HTML table with bars ---
            html = "<table class='styled-table'>"
            html += '''
            <tr>
                <th width="10%">GPU</th>
                <th width="35%">Memory Usage</th>
                <th width="25%">Utilization %</th>
                <th width="15%">Temp °C</th>
                <th width="15%">Users</th>
            </tr>
            '''

            for _, row in df.iterrows():
                html += f"<tr><td>{row['GPU']}</td>"

                mem_pct = row['memory_pct']
                mem_text = f"{mem_pct:.1f}% ({int(row['memory_used'])}/{int(row['memory_total'])})MB"
                html += f'<td><div class="bar-container"><div class="bar" style="width: {mem_pct}%; background-color: #a5d6a7;"></div><div class="bar-text">{mem_text}</div></div></td>'

                util_pct = row['util_pct']
                html += f'<td><div class="bar-container"><div class="bar" style="width: {util_pct}%; background-color: #fff59d;"></div><div class="bar-text">{util_pct}%</div></div></td>'

                temp = row['Temp °C']
                normalized_temp = min(temp, 100)
                html += f'<td><div class="bar-container"><div class="bar" style="width: {normalized_temp}%; background-color: #90caf9;"></div><div class="bar-text">{temp}°C</div></div></td>'

                html += f"<td>{row['Users']}</td></tr>"

            html += "</table>"

            st.markdown(html, unsafe_allow_html=True)
