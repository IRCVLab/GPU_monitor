# IRCV 서버 모니터링 운영 매뉴얼

## 구조 개요
- **monitor.py (Flask API)**: 서버별 SSH 세션을 유지하며 GPU/CPU/스토리지 수집기를 돌려 `/stats`, `/stats/<alias>`, `/reload/<alias>`, `/notes/<alias>`를 제공합니다.
- **monitoring_core/collectors/**: 리소스별 수집기 플러그인 (GPU, CPU, Storage). 서버마다 독립 인스턴스를 사용합니다.
- **Streamlit UI (app.py)**: API 결과를 표시하고, 다크 테마/레이아웃/메모 기능을 제공합니다. 사용자 설정(핀/컬럼수)은 URL query params로 저장됩니다.
- **데이터 흐름**: SSH → collector 명령 실행 → 정규화된 JSON → Flask → Streamlit fetch → 테이블/바차트/메모 렌더.

## 실행 방법
1) 백엔드(Fast refresh)
```bash
# 가상환경(필요 시) 활성화 후
python monitor.py
# 기본 포트 5001, CORS ON
```
2) 프론트(스트림릿)
```bash
streamlit run app.py
```
- 기본 다크모드. `.streamlit/config.toml`에 `[theme] base="dark"` 설정이 있습니다.

## 설정 포인트
- **서버 목록**: `monitor.py`의 `hosts` 배열. `(alias, host, port, user, password)` 형태. 새 서버 추가 시 이곳에 항목 추가.
- **SSH 요건**: 원격 서버에 `gpustat`, `python3`가 있어야 하며, `/proc/stat`, `/proc/meminfo`, `/proc/mounts`, `statvfs` 접근 가능해야 합니다.
- **수집 주기**: 성공 시 5초 간격, 실패 시 15초 대기. Storage 수집기는 collector 내부에서 기본 60초 캐시 → 필요 시 `StorageCollector(cache_ttl_seconds=0)`로 즉시 업데이트 모드 가능.
- **노트 저장**: `notes_store.json` 로컬 파일에 저장(자동 삭제 없음). `/notes/<alias>` GET/POST/DELETE.
- **로그**: `server.log` 로테이팅(10MB × 5). `.gitignore`에 포함.

## UI 사용법
- **상단 핀 & 칼럼수**: “⚙️ 정렬 / 레이아웃”에서 핀 목록과 칼럼 수(1/2/3) 지정 → URL query param에 저장되어 새로고침 후에도 유지.
- **메모**: 카드 우측 `✏️` 아이콘 → 인라인 편집/저장/삭제. 저장 시 현재 카드만 리프레시. 메모는 API를 통해 공유.
- **갱신**: 카드 우측 `⟳` 버튼으로 해당 서버만 강제 수집 요청.

## 새 서버 추가 절차
1) 원격 서버 준비: `gpustat`, `python3` 설치. `/proc/*`와 `statvfs` 사용 가능한지 확인.
2) `monitor.py`의 `hosts` 리스트에 `(alias, host, port, user, password)` 추가.
3) 모니터링 백엔드 재시작(`python monitor.py` 재기동).
4) 스트림릿 페이지 새로고침 후 카드가 나타나는지 확인.

## 커스터마이즈 팁
- **수집기 추가**: `monitoring_core/collectors`에 새 collector 작성 후 `build_registry()`에 등록. 스키마는 `docs/metrics_schema.md` 참고.
- **스토리지 필터**: 네트워크/NAS/작은 파티션은 `StorageCollector`에서 필터링. 필요 시 `IGNORED_FS`, `MIN_CAPACITY_BYTES` 조정.
- **테마/레이아웃**: `.streamlit/config.toml`로 기본 테마, `app.py`의 레이아웃 설정으로 카드 폭/칼럼 수 조정.
- **부하 관리**: `docs/monitoring_overhead.md` 참고. Storage 캐시 TTL, 모니터 루프 주기 등을 늘리면 I/O 감소.

