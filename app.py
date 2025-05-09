# app.py

import streamlit as st

# 반드시 가장 첫 줄에 한 번만!
st.set_page_config(page_title="GPU Dashboard", layout="wide")

from streamlit_autorefresh import st_autorefresh
import pandas as pd
import requests
import altair as alt

# 자동 새로고침 (5초)
st_autorefresh(interval=5000, key="refresh")

st.title("🖥️ GPU 서버 모니터링 대시보드")
API_BASE = "http://localhost:5001"

# 전체 서버 상태 한 번에 가져오기
try:
    resp = requests.get(f"{API_BASE}/stats", timeout=3)
    resp.raise_for_status()
    all_stats = resp.json()
except Exception as e:
    st.error(f"서버 정보 조회 실패: {e}")
    st.stop()

aliases = list(all_stats.keys())
# 2×2 그리드로
for i in range(0, len(aliases), 2):
    cols = st.columns(2)
    for alias, col in zip(aliases[i:i+2], cols):
        data = all_stats[alias]
        with col:
            st.subheader(alias)
            if st.button("⟳ Reload", key=f"reload_{alias}"):
                try:
                    r = requests.post(f"{API_BASE}/reload/{alias}", timeout=3)
                    r.raise_for_status()
                    st.success("갱신 요청 완료")
                except Exception as e:
                    st.error(f"재갱신 실패: {e}")

            if not data or "gpus" not in data:
                st.write("데이터 없음")
                continue

            # 1) 원본 데이터에서 필요한 값 뽑아 리스트 생성
            rows = []
            for gpu in data["gpus"]:
                used = gpu["memory.used"]
                total = gpu["memory.total"]
                pct = used / total * 100
                util = gpu.get("utilization.gpu", gpu.get("utilization", 0))
                users = ", ".join({p["username"] for p in gpu.get("processes", [])}) or "-"

                rows.append({
                    "GPU": str(gpu["index"]),
                    "memory_used": used,
                    "memory_total": total,
                    "memory_pct": round(pct, 1),
                    "util_pct": round(util, 1),
                    "Users": users,
                    "Temp °C": round(gpu["temperature.gpu"], 1),
                })

            df = pd.DataFrame(rows)

            # 2) 테이블용 문자열 컬럼 추가
            df["Memory Usage"] = df.apply(
                lambda r: f"{r['memory_pct']:.1f}% ({int(r['memory_used'])}/{int(r['memory_total'])})",
                axis=1
            )
            table = df[["GPU", "Memory Usage", "util_pct", "Temp °C", "Users"]].rename(
                columns={"util_pct": "Utilization %"}
            ).set_index("GPU")
            st.table(table)

            # (st.table 이후에 아래 차트 부분 대체)

            # 3) 차트용 데이터 준비
            plot_df = (
                df.reset_index()
                .melt(
                    id_vars=["GPU"],
                    value_vars=["util_pct", "memory_pct"],
                    var_name="Metric",
                    value_name="Value"
                )
            )
            # 카테고리 이름 만들기
            plot_df["Category"] = plot_df.apply(
                lambda r: f"GPU{r['GPU']} { 'Util' if r['Metric']=='util_pct' else 'Mem' }", axis=1
            )
            # Metric 레이블 맵핑
            plot_df["Metric"] = plot_df["Metric"].map({
                "util_pct": "Utilization %",
                "memory_pct": "Memory %"
            })

            # 4) 가로 막대 차트
            chart = (
                alt.Chart(plot_df)
                .mark_bar(size=8)
                .encode(
                    y=alt.Y(
                        "Category:N",
                        sort=plot_df["Category"].tolist(),
                        title=None,
                        axis=alt.Axis(labelFontSize=11)
                    ),
                    x=alt.X(
                        "Value:Q",
                        title="%",
                        scale=alt.Scale(domain=[0,100]),
                        axis=alt.Axis(format=".1f", tickMinStep=10)
                    ),
                    color=alt.Color(
                        "Metric:N",
                        scale=alt.Scale(
                            domain=["Memory %", "Utilization %"],
                            range=["steelblue", "orange"]
                        ),
                        # legend=alt.Legend(
                        #     title="Metric",
                        #     orient="bottom",
                        #     direction="horizontal"
                        # )
                    ),
                    tooltip=[
                        alt.Tooltip("Category:N", title="GPU & Metric"),
                        alt.Tooltip("Value:Q", format=".1f", title="Value (%)")
                    ]
                )
                .properties(height=300)
            )
            st.altair_chart(chart, use_container_width=True)     