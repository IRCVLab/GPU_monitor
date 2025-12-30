import altair as alt
import pandas as pd
import requests
import streamlit as st
from datetime import datetime, timedelta

# ─── Page Configuration ───────────────────────────────────────────────────────

st.set_page_config(
    page_title="GPU Statistics Dashboard",
    page_icon="📊",
    layout="wide"
)

API_BASE = "http://localhost:5001"

# ─── Helper Functions ─────────────────────────────────────────────────────────

def fetch_api(endpoint: str, params: dict = None) -> dict:
    """Fetch data from API endpoint."""
    try:
        url = f"{API_BASE}{endpoint}"
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"API 요청 실패: {e}")
        return {}
    except Exception as e:
        st.error(f"데이터 처리 오류: {e}")
        return {}


def format_number(num: float, precision: int = 2) -> str:
    """Format number with commas."""
    return f"{num:,.{precision}f}"


# ─── Sidebar Filters ──────────────────────────────────────────────────────────

with st.sidebar:
    st.header("📌 필터 설정")

    # Date range picker
    st.subheader("기간 선택")
    default_start = datetime.now() - timedelta(days=7)
    default_end = datetime.now()

    date_range = st.date_input(
        "날짜 범위",
        value=(default_start, default_end),
        max_value=datetime.now(),
        help="통계를 조회할 기간을 선택하세요"
    )

    if len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = default_start.date()
        end_date = default_end.date()

    # Server selector
    st.subheader("서버 선택")
    all_servers = ["All", "00Poseidon", "01Hinton", "02Turing", "03Lecun", "04ACE", "05NEO"]
    selected_servers = st.multiselect(
        "조회할 서버",
        options=all_servers,
        default=["All"],
        help="특정 서버만 보려면 선택하세요"
    )

    # Granularity selector
    st.subheader("데이터 세분화")
    granularity_map = {
        "원본 데이터 (1분)": "raw",
        "시간별 집계": "hourly",
        "일별 집계": "daily"
    }
    granularity_label = st.selectbox(
        "시간 단위",
        options=list(granularity_map.keys()),
        index=1,
        help="긴 기간 조회 시 집계 단위 사용 권장"
    )
    granularity = granularity_map[granularity_label]

    st.divider()

    # Refresh button
    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ─── Main Dashboard ───────────────────────────────────────────────────────────

st.title("📊 GPU 사용 통계 및 분석 대시보드")
st.caption(f"조회 기간: {start_date} ~ {end_date}")

# Prepare API parameters
api_params = {
    'start_date': datetime.combine(start_date, datetime.min.time()).isoformat(),
    'end_date': datetime.combine(end_date, datetime.max.time()).isoformat(),
}

if selected_servers and "All" not in selected_servers:
    api_params['servers'] = ','.join(selected_servers)

# ─── Overview Metrics ─────────────────────────────────────────────────────────

st.header("📈 주요 지표")

with st.spinner("통계 데이터를 불러오는 중..."):
    summary_data = fetch_api("/api/stats/summary", api_params)

if summary_data and 'error' not in summary_data:
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "총 GPU 사용 시간",
            f"{format_number(summary_data.get('total_gpu_hours', 0))} 시간"
        )

    with col2:
        st.metric(
            "평균 GPU 사용률",
            f"{format_number(summary_data.get('avg_utilization', 0), 1)}%"
        )

    with col3:
        st.metric(
            "활성 사용자 수",
            f"{summary_data.get('active_users', 0)} 명"
        )

    with col4:
        st.metric(
            "피크 시간대",
            summary_data.get('peak_hour', 'N/A')
        )
else:
    st.warning("통계 데이터를 불러올 수 없습니다. 데이터 수집이 시작되지 않았거나 오류가 발생했습니다.")

st.divider()

# ─── Time-Series Line Chart ───────────────────────────────────────────────────

st.header("📉 시간별 GPU 사용률 추이")

with st.spinner("시계열 데이터를 불러오는 중..."):
    timeseries_params = {**api_params, 'granularity': granularity}
    timeseries_data = fetch_api("/api/stats/timeseries", timeseries_params)

if timeseries_data and not isinstance(timeseries_data, dict):
    df_timeseries = pd.DataFrame(timeseries_data)

    if not df_timeseries.empty:
        # Convert timestamp to datetime
        df_timeseries['timestamp'] = pd.to_datetime(df_timeseries['timestamp'])

        # Create line chart
        chart = alt.Chart(df_timeseries).mark_line(point=True).encode(
            x=alt.X('timestamp:T', title='시간'),
            y=alt.Y('metric_value:Q', title='GPU 사용률 (%)', scale=alt.Scale(domain=[0, 100])),
            color=alt.Color('server_alias:N', legend=alt.Legend(title='서버'), scale=alt.Scale(scheme='category10')),
            tooltip=[
                alt.Tooltip('timestamp:T', title='시간', format='%Y-%m-%d %H:%M'),
                alt.Tooltip('server_alias:N', title='서버'),
                alt.Tooltip('metric_value:Q', title='사용률 (%)', format='.1f')
            ]
        ).properties(
            height=400
        ).interactive()

        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("선택한 기간에 데이터가 없습니다.")
else:
    st.warning("시계열 데이터를 불러올 수 없습니다.")

st.divider()

# ─── User Analytics ───────────────────────────────────────────────────────────

st.header("👥 사용자별 GPU 사용 분석")

with st.spinner("사용자 데이터를 불러오는 중..."):
    users_data = fetch_api("/api/stats/users", api_params)

if users_data and not isinstance(users_data, dict):
    df_users = pd.DataFrame(users_data)

    if not df_users.empty:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("상위 사용자 (GPU 시간 기준)")

            # Top 10 users bar chart
            top_users = df_users.head(10)

            bar_chart = alt.Chart(top_users).mark_bar().encode(
                x=alt.X('total_gpu_hours:Q', title='GPU 사용 시간 (시간)'),
                y=alt.Y('username:N', sort='-x', title='사용자'),
                color=alt.Color('total_gpu_hours:Q', scale=alt.Scale(scheme='viridis'), legend=None),
                tooltip=[
                    alt.Tooltip('username:N', title='사용자'),
                    alt.Tooltip('total_gpu_hours:Q', title='GPU 시간', format='.2f'),
                    alt.Tooltip('servers_used:Q', title='사용 서버 수'),
                    alt.Tooltip('avg_memory_mb:Q', title='평균 메모리 (MB)', format='.2f')
                ]
            ).properties(
                height=400
            )

            st.altair_chart(bar_chart, use_container_width=True)

        with col2:
            st.subheader("GPU 사용 시간 분포")

            # Pie chart for top 10 users
            if len(top_users) > 0:
                pie_chart = alt.Chart(top_users).mark_arc(innerRadius=50).encode(
                    theta=alt.Theta('total_gpu_hours:Q', stack=True),
                    color=alt.Color('username:N', scale=alt.Scale(scheme='category20'), legend=alt.Legend(title='사용자')),
                    tooltip=[
                        alt.Tooltip('username:N', title='사용자'),
                        alt.Tooltip('total_gpu_hours:Q', title='GPU 시간', format='.2f')
                    ]
                ).properties(
                    height=400
                )

                st.altair_chart(pie_chart, use_container_width=True)
            else:
                st.info("표시할 데이터가 없습니다.")

        # User statistics table
        st.subheader("전체 사용자 통계")
        st.dataframe(
            df_users,
            column_config={
                "username": "사용자",
                "total_gpu_hours": st.column_config.NumberColumn("GPU 시간 (h)", format="%.2f"),
                "servers_used": st.column_config.NumberColumn("사용 서버 수"),
                "avg_memory_mb": st.column_config.NumberColumn("평균 메모리 (MB)", format="%.2f"),
            },
            hide_index=True,
            use_container_width=True
        )
    else:
        st.info("선택한 기간에 사용자 데이터가 없습니다.")
else:
    st.warning("사용자 데이터를 불러올 수 없습니다.")

st.divider()

# ─── Server Heatmap ───────────────────────────────────────────────────────────

st.header("🔥 서버별 사용률 히트맵 (시간대별)")

with st.spinner("히트맵 데이터를 불러오는 중..."):
    heatmap_data = fetch_api("/api/stats/heatmap", api_params)

if heatmap_data and 'data' in heatmap_data:
    df_heatmap = pd.DataFrame(heatmap_data['data'])

    if not df_heatmap.empty:
        heatmap = alt.Chart(df_heatmap).mark_rect().encode(
            x=alt.X('hour:O', title='시간 (0-23시)', axis=alt.Axis(labelAngle=0)),
            y=alt.Y('server:N', title='서버'),
            color=alt.Color(
                'utilization:Q',
                scale=alt.Scale(scheme='redyellowgreen', domain=[0, 100]),
                title='사용률 (%)'
            ),
            tooltip=[
                alt.Tooltip('server:N', title='서버'),
                alt.Tooltip('hour:O', title='시간'),
                alt.Tooltip('utilization:Q', title='평균 사용률 (%)', format='.1f')
            ]
        ).properties(
            height=300
        )

        st.altair_chart(heatmap, use_container_width=True)

        st.caption("💡 색상이 짙을수록 해당 시간대에 GPU 사용률이 높습니다. 피크 시간과 한가한 시간을 파악할 수 있습니다.")
    else:
        st.info("히트맵 데이터가 없습니다.")
else:
    st.warning("히트맵 데이터를 불러올 수 없습니다.")

st.divider()

# ─── Power Consumption ────────────────────────────────────────────────────────

st.header("⚡ 전력 소비 분석")

with st.spinner("전력 데이터를 불러오는 중..."):
    power_params = {**api_params, 'granularity': granularity}
    power_data = fetch_api("/api/stats/power", power_params)

if power_data and 'timeseries' in power_data and 'summary' in power_data:
    # Power summary metrics
    summary_df = pd.DataFrame(power_data['summary'])

    if not summary_df.empty:
        st.subheader("서버별 전력 통계")

        # Display summary table
        col1, col2 = st.columns(2)

        with col1:
            st.dataframe(
                summary_df,
                column_config={
                    "server_alias": "서버",
                    "avg_power": st.column_config.NumberColumn("평균 전력 (W)", format="%.1f"),
                    "max_power": st.column_config.NumberColumn("최대 전력 (W)", format="%.1f"),
                    "min_power": st.column_config.NumberColumn("최소 전력 (W)", format="%.1f"),
                    "records": st.column_config.NumberColumn("측정 횟수"),
                },
                hide_index=True,
                use_container_width=True
            )

        with col2:
            # Calculate total power statistics
            total_avg_power = summary_df['avg_power'].sum()
            total_max_power = summary_df['max_power'].sum()

            st.metric("전체 평균 전력", f"{total_avg_power:.1f}W")
            st.metric("전체 최대 전력", f"{total_max_power:.1f}W")

            # Calculate energy consumption (kWh)
            # Assuming data collected every minute
            hours = (end_date - start_date).days * 24
            if hours > 0:
                energy_kwh = (total_avg_power * hours) / 1000
                st.metric("추정 전력 소비량", f"{energy_kwh:.2f} kWh")

    # Power time-series chart
    timeseries_df = pd.DataFrame(power_data['timeseries'])

    if not timeseries_df.empty:
        st.subheader("시간별 전력 소비 추이")

        timeseries_df['timestamp'] = pd.to_datetime(timeseries_df['timestamp'])

        power_chart = alt.Chart(timeseries_df).mark_line(point=True).encode(
            x=alt.X('timestamp:T', title='시간'),
            y=alt.Y('avg_power:Q', title='전력 (W)', scale=alt.Scale(domain=[0, timeseries_df['avg_power'].max() * 1.1])),
            color=alt.Color('server_alias:N', legend=alt.Legend(title='서버'), scale=alt.Scale(scheme='category10')),
            tooltip=[
                alt.Tooltip('timestamp:T', title='시간', format='%Y-%m-%d %H:%M'),
                alt.Tooltip('server_alias:N', title='서버'),
                alt.Tooltip('avg_power:Q', title='전력 (W)', format='.1f')
            ]
        ).properties(
            height=400
        ).interactive()

        st.altair_chart(power_chart, use_container_width=True)

        st.caption("💡 전력 소비 패턴을 통해 GPU 사용 패턴을 파악할 수 있습니다. 높은 전력 = 높은 GPU 사용률")
    else:
        st.info("선택한 기간에 전력 데이터가 없습니다.")
else:
    st.warning("전력 데이터를 불러올 수 없습니다. 전력 모니터링이 활성화되어 있는지 확인하세요.")

st.divider()

# ─── System Health ────────────────────────────────────────────────────────────

with st.expander("🔧 시스템 상태 및 데이터베이스 정보"):
    health_data = fetch_api("/api/stats/health")

    if health_data and 'error' not in health_data:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("데이터 수집 상태")
            last_collection = health_data.get('last_collection')
            if last_collection:
                st.metric("마지막 수집 시간", last_collection.get('timestamp', 'N/A'))
                st.metric("수집 소요 시간", f"{last_collection.get('collection_duration_ms', 0):.1f} ms")
                st.metric("수집된 서버 수", last_collection.get('servers_collected', 0))

                errors = last_collection.get('errors')
                if errors:
                    st.error(f"수집 오류: {errors}")
                else:
                    st.success("수집 정상")
            else:
                st.warning("수집 정보 없음")

        with col2:
            st.subheader("데이터베이스 통계")
            record_counts = health_data.get('record_counts', {})
            st.metric("GPU 메트릭 레코드", f"{record_counts.get('gpu_metrics', 0):,}")
            st.metric("프로세스 메트릭 레코드", f"{record_counts.get('gpu_process_metrics', 0):,}")
            st.metric("서버 요약 레코드", f"{record_counts.get('server_summary_metrics', 0):,}")
            st.metric("데이터베이스 크기", f"{health_data.get('db_size_mb', 0):.2f} MB")
    else:
        st.error("시스템 상태 정보를 불러올 수 없습니다.")

# ─── Footer ───────────────────────────────────────────────────────────────────

st.divider()
st.caption("📊 GPU Statistics Dashboard - 관리자 전용 페이지")
st.caption("데이터는 1분마다 자동으로 수집되며, 실시간으로 업데이트됩니다.")
