import json
import math
from datetime import datetime, timezone
from typing import Callable, Dict, List, Tuple
from urllib.parse import quote, unquote_plus
from zoneinfo import ZoneInfo

import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import streamlit.components.v1 as components

SEOUL_TZ = ZoneInfo("Asia/Seoul")

PREF_QUERY_KEY = "prefs"

# -----------------------------------------------------------------------------
# Page / Theme configuration
# -----------------------------------------------------------------------------

st.set_page_config(page_title="GPU Dashboard", layout="wide")

API_BASE = "http://localhost:5001"
REFRESH_INTERVAL_MS = 5000

THEMES = {
    "light": {
        "background": "#FFFFFF",
        "text": "#0F172A",
        "muted": "#475569",
        "metric": "#1E293B",
        "bar_track": "#e2e8f0",
        "bar_label": "#0F172A",
        "bars": {"memory": "#86efac", "util": "#fde68a", "temp": "#bfdbfe"},
        "status": {
            "online": ("🟢 정상", "#16a34a"),
            "degraded": ("🟡 부분 장애", "#ca8a04"),
            "error": ("🟠 오류", "#f97316"),
            "offline": ("⚫️ 중지", "#64748b"),
        },
    },
    "dark": {
        "background": "#0B1220",
        "text": "#F8FAFC",
        "muted": "#94A3B8",
        "metric": "#F8FAFC",
        "bar_track": "#1f2937",
        "bar_label": "#F8FAFC",
        "bars": {"memory": "#22c55e", "util": "#facc15", "temp": "#38bdf8"},
        "status": {
            "online": ("🟢 정상", "#22c55e"),
            "degraded": ("🟡 부분 장애", "#facc15"),
            "error": ("🟠 오류", "#fb923c"),
            "offline": ("⚫️ 중지", "#cbd5f5"),
        },
    },
}


def init_theme_state():
    if "dark_mode" not in st.session_state:
        st.session_state.dark_mode = True
    if "note_editor" not in st.session_state:
        st.session_state.note_editor = None
    if "preferred_order" not in st.session_state:
        st.session_state.preferred_order: List[str] = []
    if "grid_cols" not in st.session_state:
        st.session_state.grid_cols = 2
    if "prefs_loaded" not in st.session_state:
        st.session_state.prefs_loaded = False
    if "prefs_cache" not in st.session_state:
        st.session_state.prefs_cache = {}
    if "prefs_changed" not in st.session_state:
        st.session_state.prefs_changed = False


def get_active_theme():
    return THEMES["dark" if st.session_state.dark_mode else "light"]


def inject_theme(theme):
    st.markdown(
        f"""
        <style>
            .stApp {{
                background-color: {theme["background"]};
                color: {theme["text"]};
            }}
            [data-testid="stMetricValue"] {{
                color: {theme["metric"]};
            }}
            .table-container {{
                width: 100%;
            }}
            .resource-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.92rem;
            }}
            .resource-table th, .resource-table td {{
                padding: 0.5rem 0.6rem;
                border-bottom: 1px solid rgba(148, 163, 184, 0.2);
                vertical-align: middle;
            }}
            .resource-table th {{
                text-align: left;
                font-weight: 600;
                color: {theme["text"]};
            }}
            .bar-cell {{
                display: flex;
                flex-direction: column;
            }}
            .bar-track {{
                background: {theme["bar_track"]};
                border-radius: 6px;
                width: 100%;
                height: 18px;
                position: relative;
                overflow: hidden;
            }}
            .bar-fill {{
                position: absolute;
                top: 0;
                left: 0;
                height: 100%;
                border-radius: 6px;
            }}
            .bar-label {{
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 0.78rem;
                font-weight: 600;
                color: {theme["bar_label"]};
            }}
            .resource-table tbody tr:hover {{
                background: rgba(148, 163, 184, 0.08);
            }}
            .alias-text {{
                font-size: 1.05rem;
                font-weight: 600;
                color: {theme["text"]};
            }}
            .status-pill {{
                display: inline-flex;
                align-items: center;
                gap: 0.25rem;
                padding: 0.1rem 0.65rem;
                border-radius: 999px;
                font-size: 0.8rem;
                border: 1px solid rgba(255,255,255,0.2);
                background: rgba(148, 163, 184, 0.15);
            }}
            .last-updated {{
                font-size: 0.8rem;
                color: {theme["muted"]};
                margin-top: 0.15rem;
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _note_state_key(alias: str) -> str:
    return f"note_text_{alias}"


def open_note_editor(alias: str, default: str | None = ""):
    st.session_state.note_editor = alias
    st.session_state[_note_state_key(alias)] = default or ""


def close_note_editor(alias: str):
    if st.session_state.get("note_editor") == alias:
        st.session_state.note_editor = None
    st.session_state.pop(_note_state_key(alias), None)


# -----------------------------------------------------------------------------
# Data helpers
# -----------------------------------------------------------------------------

def fetch_stats():
    resp = requests.get(f"{API_BASE}/stats", timeout=3)
    resp.raise_for_status()
    return resp.json()


def _read_cookie_value():
    script = """
    <script>
    const streamlit = window.streamlit;
    const match = document.cookie.match(/(?:^|; )prefs=([^;]+)/);
    if (match) streamlit.setComponentValue(match[1]);
    else streamlit.setComponentValue("");
    </script>
    """
    return components.html(script, height=0)


def _write_cookie_value(encoded: str):
    script = f"""
    <script>
    document.cookie = "prefs={encoded}; path=/";
    </script>
    """
    components.html(script, height=0)


def load_preferences():
    try:
        params = st.query_params
    except Exception:
        params = {}
    raw = params.get(PREF_QUERY_KEY)
    if raw:
        value = raw[0] if isinstance(raw, list) else raw
        try:
            prefs = json.loads(unquote_plus(value))
            if isinstance(prefs, dict):
                return prefs
        except Exception:
            pass

    cookie_raw = _read_cookie_value()
    if cookie_raw:
        try:
            prefs = json.loads(unquote_plus(cookie_raw))
            if isinstance(prefs, dict):
                return prefs
        except Exception:
            pass

    return st.session_state.get("prefs_cache", {})


def set_preferences_in_url(preferences: dict):
    payload = json.dumps(preferences, separators=(",", ":"))
    encoded = quote(payload)
    success = False
    try:
        st.query_params = {PREF_QUERY_KEY: encoded}
        success = True
    except Exception:
        pass
    if not success:
        try:
            st.experimental_set_query_params(**{PREF_QUERY_KEY: encoded})
            success = True
        except Exception:
            pass
    _write_cookie_value(encoded)
    return success


def save_preferences(preferences: dict):
    st.session_state.prefs_cache = preferences
    set_preferences_in_url(preferences)
    st.session_state.prefs_changed = False


def render_query_update():
    # st.query_params가 주소창에 반영되지 않는 환경에서는 추가 행동 없음
    return


def format_timestamp(ts: str) -> str:
    if not ts:
        return "N/A"
    try:
        ts_norm = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(SEOUL_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts


def to_number(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
        if math.isnan(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def format_bytes(num: float | int | None, precision: int = 1) -> str:
    if num is None:
        return "-"
    num = float(num)
    if num < 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    idx = 0
    while num >= 1024 and idx < len(units) - 1:
        num /= 1024
        idx += 1
    return f"{num:.{precision}f} {units[idx]}"


def clamp_percent(value: float | int | None) -> float:
    return max(0.0, min(100.0, to_number(value, 0.0)))


def bar_html(percent: float, label: str, color: str) -> str:
    pct = clamp_percent(percent)
    return (
        f"<div class='bar-cell'>"
        f"<div class='bar-track'>"
        f"<div class='bar-fill' style='width:{pct:.1f}%; background:{color};'></div>"
        f"<div class='bar-label'>{label}</div>"
        f"</div>"
        f"</div>"
    )


def render_table(headers: List[str], rows: List[str], col_widths: List[str] | None = None) -> str:
    colgroup = ""
    if col_widths:
        col_defs = "".join(f"<col style='width:{width};'>" for width in col_widths)
        colgroup = f"<colgroup>{col_defs}</colgroup>"
    head_html = "".join(f"<th>{header}</th>" for header in headers)
    body_html = "".join(rows)
    return (
        "<div class='table-container'>"
        "<table class='resource-table'>"
        f"{colgroup}"
        f"<thead><tr>{head_html}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>"
    )


def normalize_gpu_resource(stats: dict | None) -> dict | None:
    if not stats:
        return None
    gpus = stats.get("gpus", [])
    normalized_gpus = []
    for gpu in gpus:
        memory_total = to_number(gpu.get("memory.total") or gpu.get("memory_total"), 0.0)
        memory_used = to_number(gpu.get("memory.used") or gpu.get("memory_used"), 0.0)
        memory_percent = (memory_used / memory_total * 100) if memory_total else 0.0
        normalized_gpus.append(
            {
                "id": gpu.get("index") or gpu.get("uuid"),
                "name": gpu.get("name") or gpu.get("product_name"),
                "memory": {
                    "used": memory_used,
                    "total": memory_total,
                    "percent": memory_percent,
                },
                "utilization": {
                    "gpu": to_number(gpu.get("utilization.gpu") or gpu.get("utilization"), 0.0),
                    "memory": to_number(gpu.get("utilization.memory"), 0.0),
                },
                "temperature": to_number(gpu.get("temperature.gpu") or gpu.get("temperature"), 0.0),
                "power": gpu.get("power.draw"),
                "processes": gpu.get("processes", []),
            }
        )

    summary = {
        "count": len(normalized_gpus),
        "total_memory": sum(gpu["memory"]["total"] for gpu in normalized_gpus),
        "total_memory_used": sum(gpu["memory"]["used"] for gpu in normalized_gpus),
        "avg_utilization": (
            sum(gpu["utilization"]["gpu"] for gpu in normalized_gpus) / len(normalized_gpus)
            if normalized_gpus
            else 0.0
        ),
    }
    return {"summary": summary, "gpus": normalized_gpus, "raw": stats}


def normalize_snapshot(alias: str, snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    if "resources" in snapshot:
        return snapshot
    stats = snapshot.get("stats")
    gpu_resource = normalize_gpu_resource(stats)
    if not gpu_resource:
        return None
    note_info = snapshot.get("note")
    return {
        "alias": alias,
        "resources": {"gpu": gpu_resource},
        "errors": None,
        "metadata": snapshot.get("metadata"),
        "last_updated": snapshot.get("last_updated"),
        "status": "online" if gpu_resource["gpus"] else "offline",
        "note": note_info,
    }


# -----------------------------------------------------------------------------
# UI rendering helpers
# -----------------------------------------------------------------------------


def render_gpu_tab(resource: dict | None, error: str | None, theme: dict):
    if error:
        st.warning(f"GPU 데이터를 불러오지 못했습니다: {error}")
        return
    if not resource:
        st.info("GPU 데이터가 아직 수집되지 않았습니다.")
        return

    headers = ["GPU", "메모리 사용량", "GPU Util", "온도", "사용자"]
    rows: List[str] = []
    for gpu in resource.get("gpus", []):
        memory = gpu.get("memory", {}) or {}
        utilization = gpu.get("utilization", {}) or {}
        temp = to_number(gpu.get("temperature"), 0.0)
        mem_used = to_number(memory.get("used"), 0.0)
        mem_total = to_number(memory.get("total"), 0.0)
        mem_pct = (mem_used / mem_total * 100) if mem_total else 0.0
        util_pct = to_number(utilization.get("gpu"), 0.0)
        mem_text = f"{mem_used:.0f}/{mem_total:.0f} MB ({mem_pct:.1f}%)"
        util_text = f"{util_pct:.1f}%"
        temp_text = f"{temp:.0f}°C"
        users = sorted({proc.get("username", "") for proc in gpu.get("processes", []) if proc.get("username")})
        rows.append(
            "<tr>"
            f"<td>{gpu.get('id')}</td>"
            f"<td>{bar_html(mem_pct, mem_text, theme['bars']['memory'])}</td>"
            f"<td>{bar_html(util_pct, util_text, theme['bars']['util'])}</td>"
            f"<td>{bar_html(temp, temp_text, theme['bars']['temp'])}</td>"
            f"<td>{', '.join(users) if users else '-'}</td>"
            "</tr>"
        )

    if not rows:
        st.info("GPU 정보가 비어있습니다.")
        return

    col_widths = ["1%", "32.5%", "32.5%", "12%", "12%"]
    table_html = render_table(headers, rows, col_widths)
    st.markdown(table_html, unsafe_allow_html=True)


def render_cpu_tab(resource: dict | None, error: str | None, theme: dict):
    if error:
        st.warning(f"CPU 데이터를 불러오지 못했습니다: {error}")
        return
    if not resource:
        st.info("CPU 데이터가 아직 수집되지 않았습니다.")
        return

    summary = resource.get("summary", {})
    headers = ["항목", "사용량"]
    rows = []

    cpu_pct = to_number(summary.get("cpu_percent"), 0.0)
    rows.append(
        "<tr>"
        "<td>CPU 사용률</td>"
        f"<td>{bar_html(cpu_pct, f'{cpu_pct:.1f}%', theme['bars']['util'])}</td>"
        "</tr>"
    )

    memory = summary.get("memory") or {}
    mem_used = to_number(memory.get("used"), 0.0)
    mem_total = to_number(memory.get("total"), 0.0)
    mem_pct = to_number(memory.get("percent"), 0.0)
    if mem_total > 0:
        mem_pct = (mem_used / mem_total * 100) if mem_total else mem_pct
        mem_text = f"{format_bytes(mem_used, 2)} / {format_bytes(mem_total, 2)} ({mem_pct:.1f}%)"
    else:
        mem_text = f"{mem_pct:.1f}% 사용"
    rows.append(
        "<tr>"
        "<td>RAM 사용률</td>"
        f"<td>{bar_html(mem_pct, mem_text, theme['bars']['memory'])}</td>"
        "</tr>"
    )

    table_html = render_table(headers, rows, ["30%", "70%"])
    st.markdown(table_html, unsafe_allow_html=True)


def render_storage_tab(resource: dict | None, error: str | None, theme: dict):
    if error:
        st.warning(f"스토리지 데이터를 불러오지 못했습니다: {error}")
        return
    if not resource:
        st.info("스토리지 데이터가 아직 수집되지 않았습니다.")
        return

    mounts = resource.get("mounts", [])

    IGNORED_FS = {"nfs", "cifs", "smb3", "smbfs", "devpts", "hugetlbfs", "mqueue", "tmpfs"}
    MIN_DISPLAY_BYTES = 5 * 1024 * 1024 * 1024  # 5GB

    filtered = []
    for mount in mounts:
        fs_type = (mount.get("fs_type") or "").lower()
        mount_path = (mount.get("mount") or "").lower()
        device = mount.get("device") or ""
        size = to_number(mount.get("size"), 0.0)
        if fs_type in IGNORED_FS:
            continue
        if size < MIN_DISPLAY_BYTES:
            continue
        if "nas" in mount_path or mount_path.startswith("/boot/efi"):
            continue
        if not device.startswith("/dev/"):
            continue
        filtered.append(mount)

    if not filtered:
        st.info("표시할 로컬 마운트가 없습니다.")
        return

    total = sum(to_number(m.get("size"), 0.0) for m in filtered)
    used = sum(to_number(m.get("used"), 0.0) for m in filtered)
    pct = (used / total * 100) if total else 0.0

    st.metric("전체 스토리지 사용률", f"{pct:.1f}% ({format_bytes(used, 2)} / {format_bytes(total, 2)})")

    headers = ["마운트 지점", "디바이스", "용량"]
    rows = []
    for mount in filtered:
        percent = to_number(mount.get("percent"), 0.0)
        label = f"{format_bytes(mount.get('used'), 1)} / {format_bytes(mount.get('size'), 1)} ({percent:.1f}%)"
        rows.append(
            "<tr>"
            f"<td>{mount.get('mount')}</td>"
            f"<td>{mount.get('device')}</td>"
            f"<td>{bar_html(percent, label, theme['bars']['memory'])}</td>"
            "</tr>"
        )

    table_html = render_table(headers, rows, ["28%", "18%", "54%"])
    st.markdown(table_html, unsafe_allow_html=True)


def render_server_card(alias: str, snapshot: dict | None, theme: dict):
    alias_label = alias[2:] if alias[:2].isdigit() else alias
    status_key = (snapshot or {}).get("status", "offline")
    status_label, status_color = theme["status"].get(status_key, ("⚪️ 미확인", theme["muted"]))
    last_updated = format_timestamp((snapshot or {}).get("last_updated"))
    note_info = (snapshot or {}).get("note") or {}
    note_content = note_info.get("content") or ""
    note_timestamp = note_info.get("timestamp")

    header_cols = st.columns([0.7, 0.15, 0.15])
    with header_cols[0]:
        header_html = (
            f"<div class='alias-text'>{alias_label} "
            f"<span class='status-pill' style='border-color:{status_color}; color:{status_color};'>{status_label}</span>"
            f"</div>"
            f"<div class='last-updated'>업데이트 {last_updated}</div>"
        )
        st.markdown(header_html, unsafe_allow_html=True)
    with header_cols[1]:
        if st.button("✏️", key=f"note_btn_{alias}", use_container_width=True):
            if st.session_state.get("note_editor") == alias:
                close_note_editor(alias)
            else:
                open_note_editor(alias, note_content)
    with header_cols[2]:
        if st.button("⟳", key=f"reload_{alias}", use_container_width=True):
            try:
                resp = requests.post(f"{API_BASE}/reload/{alias}", timeout=3)
                resp.raise_for_status()
                st.toast(f"{alias_label} 재요청 완료", icon="✅")
            except Exception as exc:
                st.error(f"갱신 실패: {exc}")

    if not snapshot:
        st.error("서버 연결 실패 또는 데이터 없음")
        return

    resources = snapshot.get("resources") or {}
    errors = snapshot.get("errors") or {}

    tab_labels: List[str] = []
    tab_payloads: List[Tuple[Callable, dict | None, str | None]] = []

    if resources.get("gpu") or errors.get("gpu"):
        tab_labels.append("GPU")
        tab_payloads.append((render_gpu_tab, resources.get("gpu"), errors.get("gpu")))
    if resources.get("cpu") or errors.get("cpu"):
        tab_labels.append("CPU")
        tab_payloads.append((render_cpu_tab, resources.get("cpu"), errors.get("cpu")))
    if resources.get("storage") or errors.get("storage"):
        tab_labels.append("Storage")
        tab_payloads.append((render_storage_tab, resources.get("storage"), errors.get("storage")))

    if not tab_labels:
        st.info("표시할 리소스가 없습니다.")
        return

    tabs = st.tabs(tab_labels)
    for tab, (renderer, resource_data, error_msg) in zip(tabs, tab_payloads):
        with tab:
            renderer(resource_data, error_msg, theme)

    editor_alias = st.session_state.get("note_editor")
    text_key = _note_state_key(alias)
    if editor_alias == alias:
        if text_key not in st.session_state:
            st.session_state[text_key] = note_content or ""
        with st.form(key=f"note_form_{alias}"):
            st.text_area(
                "메모 입력",
                key=text_key,
                height=80,
                max_chars=300,
                placeholder="사용 목적이나 종료 예정일 등을 적어주세요.",
                label_visibility="collapsed",
            )
            btn_cols = st.columns(3)
            save_clicked = btn_cols[0].form_submit_button("💾 저장")
            delete_clicked = btn_cols[1].form_submit_button("🗑 삭제")
            cancel_clicked = btn_cols[2].form_submit_button("취소")

        if save_clicked:
            note_value = st.session_state.get(text_key, "").strip()
            try:
                resp = requests.post(
                    f"{API_BASE}/notes/{alias}",
                    json={"content": note_value},
                    timeout=3,
                )
                resp.raise_for_status()
                st.toast("메모가 저장되었습니다.", icon="📝")
                close_note_editor(alias)
                st.rerun()
            except Exception as exc:
                st.error(f"메모 저장 실패: {exc}")

        if delete_clicked:
            try:
                resp = requests.delete(f"{API_BASE}/notes/{alias}", timeout=3)
                resp.raise_for_status()
                st.toast("메모가 삭제되었습니다.", icon="🗑")
                close_note_editor(alias)
                st.rerun()
            except Exception as exc:
                st.error(f"메모 삭제 실패: {exc}")

        if cancel_clicked:
            close_note_editor(alias)
            st.rerun()

    else:
        if note_content:
            note_caption = f"📝 {note_content}"
            if note_timestamp:
                note_caption += f" · {format_timestamp(note_timestamp)}"
            st.caption(note_caption)


# -----------------------------------------------------------------------------
# Main app
# -----------------------------------------------------------------------------

def main():
    init_theme_state()
    if not st.session_state.prefs_loaded:
        prefs = load_preferences()
        if prefs:
            st.session_state.preferred_order = prefs.get("order", st.session_state.preferred_order)
            if prefs.get("grid_cols") in (1, 2, 3):
                st.session_state.grid_cols = prefs["grid_cols"]
        st.session_state.prefs_loaded = True
    st.session_state.dark_mode = st.toggle("🌙 Dark Mode", value=st.session_state.dark_mode)
    theme = get_active_theme()
    inject_theme(theme)

    st.title("🖥️ GPU 서버 모니터링 대시보드")
    st.info(
        "서버 주소가 변경되었습니다. 자세한 내용은 "
        "[노션 공지](https://www.notion.so/ircv/27c0b39c7ed380a2a1acf26a2aa1bf9b?source=copy_link)를 참고하세요."
    )
    st.caption(f"데이터는 {REFRESH_INTERVAL_MS/1000:.0f}초마다 자동 새로고침 됩니다.")
    st.write("query params:", dict(st.query_params))
    st.write("parsed prefs:", load_preferences())
    st_autorefresh(interval=REFRESH_INTERVAL_MS, key="refresh")

    try:
        raw_stats = fetch_stats()
    except Exception as exc:
        st.error(f"모니터링 서버에 연결할 수 없습니다: {exc}")
        return

    all_stats: Dict[str, dict | None] = {
        alias: normalize_snapshot(alias, snapshot) for alias, snapshot in raw_stats.items()
    }

    aliases = sorted(all_stats.keys())

    with st.expander("⚙️ 정렬 / 레이아웃"):
        pinned = st.multiselect(
            "상단에 고정할 서버 (선택 순서대로 표시)",
            options=aliases,
            default=[a for a in st.session_state.preferred_order if a in aliases],
            help="딱 원하는 순서로 표시하려면 선택 순서를 바꾸세요.",
        )
        st.session_state.preferred_order = pinned
        st.session_state.grid_cols = st.radio(
            "가로 칼럼 수",
            options=[1, 2, 3, 4],
            index=[1, 2, 3, 4].index(st.session_state.grid_cols if st.session_state.grid_cols in (1, 2, 3, 4) else 2),
            horizontal=True,
        )
        save_preferences({"order": st.session_state.preferred_order, "grid_cols": st.session_state.grid_cols})

    ordered = list(st.session_state.preferred_order) + [a for a in aliases if a not in st.session_state.preferred_order]

    grid_size = st.session_state.grid_cols or 2
    for row_start in range(0, len(ordered), grid_size):
        row_aliases = ordered[row_start : row_start + grid_size]
        cols = st.columns(len(row_aliases))
        for alias, col in zip(row_aliases, cols):
            with col:
                render_server_card(alias, all_stats.get(alias), theme)
    # 주소창 쿼리스트링을 한 번 더 강제 반영
    render_query_update()


if __name__ == "__main__":
    main()
