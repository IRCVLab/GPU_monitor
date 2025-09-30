# app.py

import streamlit as st
import pandas as pd
import requests
from streamlit_autorefresh import st_autorefresh

# --- Theme Configuration ---

st.set_page_config(page_title="GPU Dashboard", layout="wide")

# Initialize session state for the dark mode toggle
if 'dark_mode' not in st.session_state:
    st.session_state.dark_mode = False

# --- CSS Definitions ---

# Base CSS shared by both themes
base_css = """
    .status-dot {
        height: 10px;
        width: 10px;
        border-radius: 50%;
        display: inline-block;
        margin-right: 8px;
    }
    .server-title { display: flex; align-items: center; gap: 10px; }
    .styled-table { border-collapse: collapse; width: 100%; margin: auto; }
    .bar-container { position: relative; width: 100%; height: 20px; border-radius: 3px; }
    .bar { position: absolute; height: 100%; border-radius: 3px; }
"""

# Light Theme Specific CSS
light_theme_css = base_css + """
    .stApp {
        background-color: #FFFFFF;
        color: #000000;
    }
    h1, h2, h3, h4, h5, h6 { color: #000000; }
    div[data-testid="stToggle"] label {
        color: #000000;
    }
    .stButton>button {
        background-color: #FFFFFF;
        color: #31333F;
        border: 1px solid #DCDCDC;
    }
    .status-ok { background-color: #28a745; }
    .status-fail { background-color: #dc3545; }
    .last-update-time { font-size: 0.8em; color: #6c757d; margin-left: auto; }
    .styled-table th, .styled-table td { padding: 8px; text-align: left; border-bottom: 1px solid #ddd; color: #000;}
    .bar-container { background-color: #f5f5f5; }
    .bar-text { position: absolute; width: 100%; text-align: center; color: #333; z-index: 2; line-height: 20px; font-size: 0.9em; font-weight: 500; }
    .bar-mem { background-color: #a5d6a7; }
    .bar-util { background-color: #fff59d; }
    .bar-temp { background-color: #90caf9; }
    .connection-error { color: #dc3545; font-weight: bold; padding: 20px; text-align: center; background-color: #f8d7da; border: 1px solid #f5c6cb; border-radius: 5px; }
"""

# Dark Theme Specific CSS
dark_theme_css = base_css + """
    .stApp {
        background-color: #0E1117;
        color: #FAFAFA;
    }
    h1, h2, h3, h4, h5, h6 { color: #FAFAFA; }
    div[data-testid="stToggle"] label {
        color: #FFFFFF;
    }
    .stButton>button {
        background-color: #262730;
        color: #FAFAFA;
        border: 1px solid #4F4F4F;
    }
    .status-ok { background-color: #28a745; }
    .status-fail { background-color: #dc3545; }
    .last-update-time { font-size: 0.8em; color: #9a9a9a; margin-left: auto; }
    .styled-table th, .styled-table td { padding: 8px; text-align: left; border-bottom: 1px solid #4F4F4F; color: #E0E0E0; }
    .bar-container { background-color: #333333; }
    .bar-text { position: absolute; width: 100%; text-align: center; color: #FFFFFF; z-index: 2; line-height: 20px; font-size: 0.9em; font-weight: 500; }
    .bar-mem { background-color: #4CAF50; }
    .bar-util { background-color: #FFC107; }
    .bar-temp { background-color: #2196F3; }
    .connection-error { color: #EF9A9A; font-weight: bold; padding: 20px; text-align: center; background-color: rgba(239, 154, 154, 0.1); border: 1px solid #E57373; border-radius: 5px; }
"""

# --- App Layout ---

st.session_state.dark_mode = st.toggle('🌙 Dark Mode', value=st.session_state.dark_mode)

# Inject the chosen CSS
css_to_inject = dark_theme_css if st.session_state.dark_mode else light_theme_css
st.markdown(f"<style>{css_to_inject}</style>", unsafe_allow_html=True)

# 자동 새로고침 (5초)
st_autorefresh(interval=5000, key="refresh")

st.title("🖥️ GPU 서버 모니터링 대시보드")
st.info("서버 주소가 변경되었습니다. 자세한 내용은 [이 링크](https://www.notion.so/ircv/27c0b39c7ed380a2a1acf26a2aa1bf9b?source=copy_link)를 참고하세요.")
API_BASE = "http://localhost:5001"


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
        server_info = all_stats.get(alias)
        
        # Extract stats and last_updated from server_info
        stats = None
        last_update_display = "N/A"
        if isinstance(server_info, dict) and "stats" in server_info and "last_updated" in server_info:
            stats = server_info["stats"]
            last_update_display = server_info["last_updated"]

        with col:
            # --- Server Title with Status Dot and Last Update Time ---
            status_class = "status-ok" if stats else "status-fail"
            
            st.markdown(
                f'<div class="server-title"><span class="status-dot {status_class}"></span><h3>{alias[2:]}</h3><span class="last-update-time">Last Update: {last_update_display}</span></div>',
                unsafe_allow_html=True
            )

            if st.button("⟳ Reload", key=f"reload_{alias}"):
                try:
                    r = requests.post(f"{API_BASE}/reload/{alias}", timeout=3)
                    r.raise_for_status()
                    st.toast("갱신 요청 완료", icon="✅")
                except Exception as e:
                    st.error(f"재갱신 실패: {e}")

            if not stats:
                st.markdown('<div class="connection-error">서버 연결 실패</div>', unsafe_allow_html=True)
                continue

            # 1) 원본 데이터에서 필요한 값 뽑아 리스트 생성
            rows = []
            for gpu in stats.get("gpus", []):
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
                html += f'<td><div class="bar-container"><div class="bar bar-mem" style="width: {mem_pct}%;"></div><div class="bar-text">{mem_text}</div></div></td>'

                util_pct = row['util_pct']
                html += f'<td><div class="bar-container"><div class="bar bar-util" style="width: {util_pct}%;"></div><div class="bar-text">{util_pct}%</div></div></td>'

                temp = row['Temp °C']
                normalized_temp = min(temp, 100)
                html += f'<td><div class="bar-container"><div class="bar bar-temp" style="width: {normalized_temp}%;"></div><div class="bar-text">{temp}°C</div></div></td>'

                html += f"<td>{row['Users']}</td></tr>"

            html += "</table>"

            st.markdown(html, unsafe_allow_html=True)
