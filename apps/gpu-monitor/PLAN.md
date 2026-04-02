# GPU 모니터링 v3 — 프로젝트 플랜

## 배경 및 목표

| | v1 (Streamlit) | v2 (React/Next.js) | v3 (목표) |
|---|---|---|---|
| 사용률 | 높음 (가볍다) | 낮음 (무겁다) | 높음 |
| 디자인 | 부족 | 좋음 | v2 수준 |
| 배포 복잡도 | 낮음 | 높음 (Docker 4개) | 낮음 |
| 수집 방식 | SSH pull | Agent push | SSH pull |
| 외부 서버 지원 | 없음 | 어렵 (에이전트 설치) | SSH pull로 동일 |

**핵심 원칙: 가볍고, 빠르고, 필요한 정보만.**

---

## 확정된 기술 스택

```
Frontend  : SvelteKit (Next.js 대비 번들 ~10배 작음, SSR 내장)
Backend   : FastAPI (비동기, 빠름)
Database  : SQLite (PostgreSQL 불필요, 단일 파일)
수집방식  : SSH pull — Paramiko (에이전트 설치 불필요)
실시간    : WebSocket (polling보다 효율적)
인증      : JWT (7일 만료, refresh token 포함)
알림      : Slack webhook + slash command
```

---

## 아키텍처

```
Browser (SvelteKit)
    │
    ├─ REST API  ──────────────────────────────┐
    └─ WebSocket ──────────────────────────────┤
                                               ▼
                                    FastAPI Backend
                                    (SQLite DB)
                                         │
                             SSH outbound (Paramiko)
                                    ┌────┴────┐
                              내부망 서버   외부망 서버
                              (password    (SSH key
                               or key)      auth)
```

모니터링 서버에서 아웃바운드 SSH로 모든 서버에 접근.
에이전트 설치 불필요. 서버 등록 = SSH 접속 정보 입력만.

---

## 기능 범위

### 포함 (MVP)

- [ ] **실시간 대시보드**: GPU utilization / memory / temp / power / 사용 유저
- [ ] **서버 상태**: 온라인 / 오프라인 / 경고, 마지막 업데이트 시간
- [ ] **탭 구분**: 내부망 / 외부망 서버 분리 표시
- [ ] **CPU/RAM 표시**: 접었다 펼치기 (secondary info)
- [ ] **서버 메모**: 서버당 노트, 인증 후 작성/삭제
- [ ] **서버 순서 변경**: 드래그로 GUI 재정렬, 쿠키 저장
- [ ] **다크/라이트 모드**: 기본 다크, 설정 쿠키 저장
- [ ] **서버 등록 UI**: 관리자 패스워드 인증 후, SSH host/port/user + password or key 입력
- [ ] **Slack 알림**: 상태 변화 채널 로그
- [ ] **Slack /gpu 커맨드**: 슬랙에서 현황 조회
- [ ] **24h 사용 히트맵**: 서버×시간대 사용률 요약

### 제외 (의도적으로 안 만듦)

- Notion 연동
- LLM storage 분석
- DnD 카드 정렬 (단순 순서 설정으로 대체)
- 유저 그룹
- TimescaleDB
- DM 알림 (채널 로그만)
- 복잡한 alert rule 시스템

---

## 화면 구성

### 메인 대시보드

```
┌─ [내부망] [외부망]  ───────────── 마지막 갱신: 3초 전  👤 계정 ─┐
│                                                               │
│  ●  Poseidon                              ●  Hinton          │
│  ┌─────────────────────────────┐  ┌─────────────────────┐   │
│  │ GPU 0  ████████░░  78%      │  │ GPU 0  ██░░░░░░  24%│   │
│  │        18.2 / 24 GB  72°C   │  │        6/24GB  61°C │   │
│  │        user: jskim          │  │        (idle)       │   │
│  │ GPU 1  ████░░░░░░  40%      │  │ GPU 1  ████████  82%│   │
│  │        8/24 GB  65°C        │  │        20/24GB  79°C│   │
│  │        user: mslee           │  │        user: cwpark │   │
│  ├─────────────────────────────┤  └─────────────────────┘   │
│  │ ▼ CPU 45%  RAM 62%          │                            │
│  │ [메모 2개]  [+ 메모]         │                            │
│  └─────────────────────────────┘                            │
│                                                               │
│  ✕  Turing  오프라인 (5분 전)    ⚠  Lecun  경고             │
└───────────────────────────────────────────────────────────────┘
```

### 서버 등록

```
서버 이름 / 표시명
IP 또는 hostname
SSH 포트 (기본 22)
SSH 유저명
인증 방식: [● 비밀번호] [○ SSH 키]
  → 비밀번호: 입력
  → SSH 키: 텍스트박스에 private key 붙여넣기
네트워크: [● 내부망] [○ 외부망]
[연결 테스트] [저장]
```

---

## 개발 단계 (Phases)

### Phase 1 — 백엔드 코어 (우선순위 1)

- FastAPI 프로젝트 세팅
- SQLite 스키마 (servers, users, notes, gpu_metrics, history)
- SSH collector — Paramiko, password + key auth
- 5초 polling loop, 60초 history 아카이브
- WebSocket endpoint (실시간 push)
- REST API: 서버 CRUD, 메트릭 조회

### Phase 2 — SvelteKit 대시보드

- 프로젝트 세팅 (SvelteKit + TypeScript + Tailwind)
- 메인 대시보드 (서버 카드, GPU 바)
- WebSocket 실시간 연결
- 다크/라이트 모드 (쿠키 저장)
- 반응형 레이아웃

### Phase 3 — 쓰기 기능

- 서버 등록 UI (관리자 패스워드 인라인 입력, SSH 크리덴셜, 연결 테스트)
- 서버 순서 변경 (drag UI, 쿠키/DB 저장)
- 메모 시스템 (SSH 크리덴셜으로 본인 확인, 인라인 입력)

### Phase 4 — Slack + 히스토리

- Slack webhook: 상태 변화 알림 (스팸 방지 로직 포함)
- Slack `/gpu` slash command
- 24시간 히트맵
- 간단 통계 페이지

---

## 배포 목표

```bash
# 개발 실행
uvicorn main:app --reload          # 백엔드 (port 8000)
npm run dev                         # 프론트 (port 5173)

# 프로덕션 (단순하게)
python main.py                      # 백엔드
npm run build && npm run preview    # 프론트 (또는 nginx로 static serve)
```

Docker는 옵션 제공하되 기본 실행은 직접 실행으로.

---

## 디렉토리 구조 (목표)

```
monitoring_v2/
├── backend/
│   ├── main.py
│   ├── collectors/      # SSH collector
│   ├── routers/         # API routes
│   ├── models.py        # SQLAlchemy models
│   ├── database.py      # SQLite connection
│   ├── auth.py          # JWT
│   └── slack.py         # Slack integration
├── frontend/
│   ├── src/
│   │   ├── routes/      # SvelteKit pages
│   │   ├── components/  # UI components
│   │   └── lib/         # utils, stores
│   └── package.json
├── feature/             # 기획/설계 문서
├── PLAN.md              # 이 파일
├── CLAUDE.md            # AI 협업 가이드라인
└── README.md            # 사용법 (Phase 4 완료 후 최신화)
```
