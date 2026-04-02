import json
import math
from datetime import datetime, timezone
from html import escape
from typing import Callable, Dict, List, Tuple
from urllib.parse import quote, unquote_plus
from zoneinfo import ZoneInfo

import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
SEOUL_TZ = ZoneInfo("Asia/Seoul")

PREF_QUERY_KEY = "prefs"
DELETE_HELP_TEXT = "자신이 쓴 메모만 삭제 가능합니다 (관리자는 전용 비밀번호 사용 가능)"
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
    st.session_state.setdefault("dark_mode", True)
    st.session_state.setdefault("preferred_order", [])
    st.session_state.setdefault("grid_cols", 2)
    st.session_state.setdefault("prefs_cache", {})
    st.session_state.setdefault("show_power", True)


def get_active_theme():
    return THEMES["dark" if st.session_state.dark_mode else "light"]


def inject_theme(theme):
    st.markdown(
        f"""
        <style>
            /* Hide entire sidebar on main dashboard */
            [data-testid="stSidebar"] {{
                display: none;
            }}

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
            .note-entry {{
                font-size: 0.85rem;
                margin: 0.05rem 0;
                line-height: 1.2;
            }}
            .note-meta {{
                color: {theme["muted"]};
                font-weight: 600;
                margin-right: 0.35rem;
            }}
            .note-delete-button div[data-testid="stButton"] {{
                display: inline-flex;
                margin: 0;
                padding: 0;
            }}
            .note-delete-button button {{
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                border-radius: 0 !important;
                padding: 0 !important;
                min-height: 0 !important;
                height: auto !important;
                min-width: 0 !important;
                width: auto !important;
                color: {theme["muted"]} !important;
                text-decoration: underline !important;
                font-size: 0.85rem;
            }}
            .note-delete-button button p {{
                margin: 0 !important;
            }}
            .note-delete-button button:hover {{
                color: {theme["text"]} !important;
            }}
            .card-gap {{
                height: 1.2rem;
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _note_form_flag(alias: str) -> str:
    return f"note_form_visible_{alias}"


def _note_form_keys(alias: str) -> Dict[str, str]:
    return {
        "user": f"note_user_{alias}",
        "password": f"note_pw_{alias}",
        "content": f"note_content_{alias}",
    }


def _clear_note_form(alias: str):
    for key in _note_form_keys(alias).values():
        st.session_state.pop(key, None)


def _delete_state_key(alias: str) -> str:
    return f"delete_note_target_{alias}"


def _delete_form_keys(dom_key: str) -> Dict[str, str]:
    return {
        "user": f"del_user_{dom_key}",
        "password": f"del_pw_{dom_key}",
    }


def _clear_delete_form(dom_key: str):
    for key in _delete_form_keys(dom_key).values():
        st.session_state.pop(key, None)


def _note_dom_key(alias: str, note: dict, index: int) -> str:
    raw = note.get("id") or note.get("timestamp") or f"{index}"
    return f"{alias}_{raw}"


# -----------------------------------------------------------------------------
# Data helpers
# -----------------------------------------------------------------------------

def fetch_stats():
    resp = requests.get(f"{API_BASE}/stats", timeout=3)
    resp.raise_for_status()
    return resp.json()

def load_preferences() -> dict:
    return st.session_state.get("prefs_cache", {})

def save_preferences(preferences: dict):
    st.session_state.prefs_cache = preferences

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

    # Build headers conditionally
    headers = ["GPU", "메모리 사용량", "GPU Util", "온도"]
    if st.session_state.get("show_power", False):
        headers.append("전력")
    headers.append("사용자")

    rows: List[str] = []
    for gpu in resource.get("gpus", []):
        memory = gpu.get("memory", {}) or {}
        utilization = gpu.get("utilization", {}) or {}
        temp = to_number(gpu.get("temperature"), 0.0)
        mem_used = to_number(memory.get("used"), 0.0)
        mem_total = to_number(memory.get("total"), 0.0)
        mem_pct = (mem_used / mem_total * 100) if mem_total else 0.0
        util_pct = to_number(utilization.get("gpu"), 0.0)
        mem_text = f"{mem_used/1024:.1f}/{mem_total/1024:.1f} GB ({mem_pct:.1f}%)"
        util_text = f"{util_pct:.1f}%"
        temp_text = f"{temp:.0f}°C"
        users = sorted({proc.get("username", "") for proc in gpu.get("processes", []) if proc.get("username")})

        # Build power cell if toggle is on
        power_cell = ""
        if st.session_state.get("show_power", False):
            power = gpu.get('power')
            if power is not None:
                power_text = f"{power:.1f}W"
                power_pct = min(power / 300 * 100, 100)  # Assume 300W max
                # Use orange/red color for power (#ff9800 for light, #ff6b35 for dark)
                power_color = "#ff6b35" if st.session_state.dark_mode else "#ff9800"
                power_cell = f"<td>{bar_html(power_pct, power_text, power_color)}</td>"
            else:
                power_cell = "<td>N/A</td>"

        rows.append(
            "<tr>"
            f"<td>{gpu.get('id')}</td>"
            f"<td>{bar_html(mem_pct, mem_text, theme['bars']['memory'])}</td>"
            f"<td>{bar_html(util_pct, util_text, theme['bars']['util'])}</td>"
            f"<td>{bar_html(temp, temp_text, theme['bars']['temp'])}</td>"
            f"{power_cell}"
            f"<td>{', '.join(users) if users else '-'}</td>"
            "</tr>"
        )

    if not rows:
        st.info("GPU 정보가 비어있습니다.")
        return

    # Adjust column widths based on power toggle
    if st.session_state.get("show_power", False):
        col_widths = ["1%", "22%", "28%", "10%", "12%", "12%"]
    else:
        col_widths = ["1%", "28%", "35%", "12%", "12%"]

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

    note_flag = _note_form_flag(alias)
    delete_state_key = _delete_state_key(alias)
    delete_mode_key = f"delete_mode_{alias}"
    if note_flag not in st.session_state:
        st.session_state[note_flag] = False
    if delete_state_key not in st.session_state:
        st.session_state[delete_state_key] = None
    if delete_mode_key not in st.session_state:
        st.session_state[delete_mode_key] = False

    # Calculate server total power if power toggle is on
    server_power_html = ""
    if st.session_state.get("show_power", False) and snapshot:
        resources = snapshot.get('resources', {})
        gpu_data = resources.get('gpu')
        if gpu_data:
            server_total_power = 0.0
            for gpu in gpu_data.get('gpus', []):
                power = gpu.get('power')
                if power is not None:
                    server_total_power += power

            if server_total_power > 0:
                server_power_html = f" <span style='color:{theme['text']}; font-size:1.1rem; font-weight:600;'>⚡ {server_total_power:.1f}W</span>"

    header_cols = st.columns([0.76, 0.08, 0.08, 0.08])
    with header_cols[0]:
        header_html = (
            f"<div class='alias-text'>{alias_label} "
            f"<span class='status-pill' style='border-color:{status_color}; color:{status_color};'>{status_label}</span>"
            f"{server_power_html}"
            f"</div>"
            f"<div class='last-updated'>업데이트 {last_updated}</div>"
        )
        st.markdown(header_html, unsafe_allow_html=True)
    with header_cols[1]:
        form_open = st.session_state[note_flag]
        btn_label = "📝" if not form_open else "✖️"
        btn_help = "메모 작성 열기" if not form_open else "메모 작성 닫기"
        if st.button(
            btn_label,
            key=f"toggle_note_{alias}",
            help=btn_help,
            use_container_width=True,
        ):
            new_state = not form_open
            st.session_state[note_flag] = new_state
            if not new_state:
                _clear_note_form(alias)
    with header_cols[2]:
        delete_mode_on = st.session_state[delete_mode_key]
        del_label = "🗑" if not delete_mode_on else "🗑✔"
        del_help = "삭제 모드 켜기 (메모 옆에 삭제 버튼 표시)" if not delete_mode_on else "삭제 모드 끄기"
        if st.button(
            del_label,
            key=f"toggle_delete_mode_{alias}",
            help=del_help,
            use_container_width=True,
        ):
            new_state = not delete_mode_on
            st.session_state[delete_mode_key] = new_state
            if not new_state:
                active_dom_key = st.session_state.get(delete_state_key)
                if active_dom_key:
                    _clear_delete_form(active_dom_key)
                st.session_state[delete_state_key] = None
    with header_cols[3]:
        if st.button("⟳", key=f"reload_{alias}", use_container_width=True):
            try:
                resp = requests.post(f"{API_BASE}/reload/{alias}", timeout=3)
                resp.raise_for_status()
                st.toast(f"{alias_label} 재요청 완료", icon="✅")
            except Exception as exc:
                st.error(f"갱신 실패: {exc}")

    if st.session_state[note_flag]:
        form_keys = _note_form_keys(alias)
        with st.container():
            st.markdown("##### 메모 작성")
            with st.form(key=f"note_form_{alias}", clear_on_submit=False):
                creds = st.columns([0.5, 0.5])
                username = creds[0].text_input(
                    "아이디",
                    key=form_keys["user"],
                    placeholder="이름 또는 계정",
                    label_visibility="collapsed",
                )
                password = creds[1].text_input(
                    "비밀번호",
                    key=form_keys["password"],
                    placeholder="비밀번호",
                    type="password",
                    label_visibility="collapsed",
                )
                content = st.text_area(
                    "메모 내용",
                    key=form_keys["content"],
                    height=110,
                    placeholder="내용을 입력하세요",
                )
                action_cols = st.columns([0.5, 0.5])
                submit_clicked = action_cols[0].form_submit_button("저장", use_container_width=True)
                cancel_clicked = action_cols[1].form_submit_button("취소", use_container_width=True)

            if submit_clicked:
                display_user = (username or "").strip() or "관리자"
                pw_clean = (password or "").strip()
                body = content.strip()
                if not pw_clean or not body:
                    st.error("비밀번호와 내용을 모두 입력해주세요.")
                else:
                    try:
                        payload = {
                            "username": display_user,
                            "password": pw_clean,
                            "content": content,
                        }
                        resp = requests.post(
                            f"{API_BASE}/notes/{alias}",
                            json=payload,
                            timeout=3,
                        )
                        if resp.status_code == 401:
                            st.error("계정 또는 비밀번호가 올바르지 않습니다.")
                        else:
                            resp.raise_for_status()
                            st.toast("메모 저장 완료", icon="📝")
                            _clear_note_form(alias)
                            st.session_state[note_flag] = False
                            st.rerun()
                    except Exception as exc:
                        st.error(f"저장 실패: {exc}")
            elif cancel_clicked:
                _clear_note_form(alias)
                st.session_state[note_flag] = False

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

    notes = (snapshot or {}).get("notes") or []
    active_delete = st.session_state.get(delete_state_key)
    delete_mode_on = st.session_state.get(delete_mode_key, False)
    if not delete_mode_on and active_delete:
        _clear_delete_form(active_delete)
        st.session_state[delete_state_key] = None
        active_delete = None
    if notes:
        matched_delete_target = False
        for idx, note in enumerate(notes):
            dom_key = _note_dom_key(alias, note, idx)
            ts = format_timestamp(note.get("timestamp"))
            user_label = note.get("user") or "N/A"
            note_id = note.get("id")
            meta_html = f"{escape(user_label)} · {escape(ts)}"
            content_html = escape(note.get("content") or "")

            if delete_mode_on:
                cols = st.columns([0.94, 0.06])
                with cols[0]:
                    st.markdown(
                        f"<div class='note-entry'><span class='note-meta'>{meta_html}</span>"
                        f"<span class='note-content'>{content_html}</span></div>",
                        unsafe_allow_html=True,
                    )
                with cols[1]:
                    delete_wrap = st.container()
                    delete_wrap.markdown("<div class='note-delete-button'>", unsafe_allow_html=True)
                    delete_clicked = delete_wrap.button(
                        "삭제",
                        key=f"del_btn_{dom_key}",
                        help=DELETE_HELP_TEXT,
                    )
                    delete_wrap.markdown("</div>", unsafe_allow_html=True)
                    if delete_clicked:
                        previous_target = st.session_state.get(delete_state_key)
                        if previous_target and previous_target != dom_key:
                            _clear_delete_form(previous_target)
                        st.session_state[delete_state_key] = dom_key
                        active_delete = dom_key
            else:
                st.markdown(
                    f"<div class='note-entry'><span class='note-meta'>{meta_html}</span>"
                    f"<span class='note-content'>{content_html}</span></div>",
                    unsafe_allow_html=True,
                )

            if delete_mode_on and active_delete == dom_key:
                matched_delete_target = True
                form_keys = _delete_form_keys(dom_key)
                delete_cols = st.columns([0.45, 0.35, 0.1, 0.1])
                del_user = delete_cols[0].text_input(
                    "아이디",
                    key=form_keys["user"],
                    placeholder="아이디",
                    label_visibility="collapsed",
                )
                del_pw = delete_cols[1].text_input(
                    "비밀번호",
                    key=form_keys["password"],
                    placeholder="비밀번호",
                    type="password",
                    label_visibility="collapsed",
                )
                confirm = delete_cols[2].button("확인", key=f"del_confirm_{dom_key}")
                cancel = delete_cols[3].button("취소", key=f"del_cancel_{dom_key}")
                if confirm:
                    pw_clean = (del_pw or "").strip()
                    if not pw_clean:
                        st.error("비밀번호를 입력해주세요.")
                    else:
                        try:
                            payload = {
                                "username": (del_user or "").strip() or "관리자",
                                "password": pw_clean,
                                "note_id": note_id,
                            }
                            resp = requests.delete(
                                f"{API_BASE}/notes/{alias}",
                                json=payload,
                                timeout=3,
                            )
                            resp.raise_for_status()
                            st.toast("메모 삭제 완료", icon="🗑")
                            st.session_state[delete_state_key] = None
                            _clear_delete_form(dom_key)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"삭제 실패: {exc}")
                elif cancel:
                    st.session_state[delete_state_key] = None
                    _clear_delete_form(dom_key)
        if delete_mode_on and active_delete and not matched_delete_target:
            _clear_delete_form(active_delete)
            st.session_state[delete_state_key] = None

# -----------------------------------------------------------------------------
# Main app
# -----------------------------------------------------------------------------

def main():
    init_theme_state()
    prefs = load_preferences()
    if prefs:
        st.session_state.preferred_order = prefs.get("order", st.session_state.preferred_order)
        grid_cols_value = prefs.get("grid_cols")
        if isinstance(grid_cols_value, int) and grid_cols_value in (1, 2, 3, 4):
            st.session_state.grid_cols = grid_cols_value
        st.session_state.show_power = prefs.get("show_power", False)
                
    st.session_state.dark_mode = st.toggle("🌙 Dark Mode", value=st.session_state.dark_mode)
    theme = get_active_theme()
    inject_theme(theme)

    st.title("🖥️ GPU 서버 모니터링 대시보드")
    st.info(
        "‼️ [메모 실명제](https://newsimg.sedaily.com/2016/08/12/1L042E3BJ9_1.jpg)를 도입하였습니다. "
        "계정과 비밀번호는 해당 서버의 것을 입력하면 됩니다. ‼️"
    )
    st.caption(f"데이터는 {REFRESH_INTERVAL_MS/1000:.0f}초마다 자동 새로고침 됩니다.")
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
            index=[1, 2, 3, 4].index(
                st.session_state.grid_cols if st.session_state.grid_cols in (1, 2, 3, 4) else 2
            ),
            horizontal=True,
        )

        st.session_state.show_power = st.checkbox(
            "⚡ 전력 사용량 표시",
            value=st.session_state.get("show_power", False),
            help="GPU 전력 소비를 표시합니다 (W)"
        )

        # URL 파라미터 기반으로 개인 레이아웃을 저장
        save_preferences(
            {"order": st.session_state.preferred_order, "grid_cols": st.session_state.grid_cols, "show_power": st.session_state.show_power}
        )
    ordered = list(st.session_state.preferred_order) + [a for a in aliases if a not in st.session_state.preferred_order]

    grid_size = st.session_state.grid_cols or 2
    for row_start in range(0, len(ordered), grid_size):
        row_aliases = ordered[row_start : row_start + grid_size]
        cols = st.columns(len(row_aliases))
        for alias, col in zip(row_aliases, cols):
            with col:
                render_server_card(alias, all_stats.get(alias), theme)
        st.markdown("<div class='card-gap'></div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
