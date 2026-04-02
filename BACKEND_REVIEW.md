# 백엔드 코드 리뷰

기준:
- 이 문서는 현재 시점의 미해결 이슈만 유지한다.
- 해결된 항목이나 과거 수정 이력은 남기지 않는다.

검토 범위:
- `backend/main.py`
- `backend/config.py`
- `backend/database.py`
- `backend/models.py`
- `backend/routers/*.py`
- `backend/collectors/*.py`
- `backend/slack_client.py`
- `backend/ws_manager.py`

검증:
- 기능 문서 확인: `PLAN.md`, `feature/*.md`
- 정적 확인: `./.venv/bin/python -m compileall backend`
- 테스트 탐색: 저장소 내 자동 테스트 파일 없음
- 코드 직접 확인: 2026-03-21 기준, 각 파일 실제 코드 검토

## Open Findings

### 1. CPU/RAM 수집이 원격 `python3 + psutil` 설치에 묶여 있고 fallback이 없다

관련 파일:
- `backend/collectors/system.py:14`
- `backend/collectors/server_collector.py:127`
- `feature/collect.md:25`

문제:
- 시스템 메트릭은 원격 서버에서 `python3 -c "import psutil"`을 실행하는 방식이다.
- 설계 문서는 `/proc/stat`, `/proc/meminfo` 기반 수집을 전제로 하지만 현재 구현에는 그 fallback이 없다.

영향:
- `psutil`이 없는 서버는 SSH와 GPU 수집이 정상이어도 계속 `degraded`가 된다.
- 서버별 파이썬 패키지 상태를 운영자가 맞춰야 해서 확장성이 떨어진다.

권고:
- `/proc/stat`, `/proc/meminfo` 파싱을 기본 경로로 두고 `python3 + psutil`은 보조 수단으로 내리는 편이 안전하다.

코드 검토 결과 (2026-03-21): `system.py:14-22`에 `SYSTEM_CMD`가 psutil 단일 경로로 구현되어 있고, `server_collector.py:127-133`의 `_sync_collect_system()`에 `/proc` 기반 fallback 없음. 문제 그대로 잔존.

---

### 2. 메모 인증이 등록된 모든 서버에 동시 SSH 로그인을 시도하고, 가장 느린 서버까지 기다린다

관련 파일:
- `backend/routers/notes.py:74`
- `backend/routers/notes.py:83`

문제:
- `_verify_user()`는 모든 서버를 조회한 뒤 각 서버에 SSH 인증을 병렬로 던지고 `gather()`로 전부 끝날 때까지 기다린다.
- 첫 번째 서버에서 인증에 성공해도 느리거나 죽어 있는 다른 서버 timeout이 끝날 때까지 반환하지 않는다.

영향:
- 서버 수가 늘수록 메모 작성/삭제가 느려진다.
- note 한 번 쓸 때마다 다수의 SSH 인증 시도가 발생해 부하와 운영 잡음이 커진다.

권고:
- 첫 성공 시 즉시 반환하도록 `asyncio.as_completed()` 기반으로 바꾸는 편이 낫다.
- 최소한 note 인증용 서버 집합을 줄이거나 짧은 캐시를 두는 것이 필요하다.

코드 검토 결과 (2026-03-21): `notes.py:83-96`에서 `asyncio.gather(*tasks, return_exceptions=True)` 그대로 사용. 첫 성공 후 조기 종료 없음. `_ssh_auth_check()`의 timeout=10이므로 서버 N대 환경에서 최악 10초 블록. 문제 그대로 잔존.

---

### 3. 서버 수정 시 기존 credential을 명시적으로 제거할 수 없어서 인증 방식 전환 semantics가 불명확하다

관련 파일:
- `backend/routers/servers.py:64`
- `backend/routers/servers.py:217`
- `backend/routers/servers.py:219`
- `backend/collectors/ssh_client.py:39`

문제:
- `ServerUpdate`는 field가 전달되지 않으면 기존 값을 유지하고, 빈 값으로 "삭제"를 표현하는 스키마도 없다.
- `SSHClient.connect()`는 `ssh_private_key`가 남아 있으면 항상 비밀번호보다 우선 사용한다.

영향:
- 키 기반 서버를 비밀번호 기반으로 바꾸려 해도 이전 키가 남아 있으면 계속 키로 접속한다.
- credential 정리/교체 정책이 API 수준에서 명확하지 않다.

권고:
- credential마다 `keep/delete/replace` semantics를 분리하거나, 반대편 credential을 명시적으로 `NULL` 처리하는 API가 필요하다.

코드 검토 결과 (2026-03-21): `servers.py:217-220`에서 password/key 각각 `is not None`일 때만 덮어쓰는 패턴 유지. `ssh_client.py:39-44`에서 `ssh_private_key` 있으면 key 우선 고정. `ServerUpdate` 스키마에 "삭제" 표현 수단 없음. 문제 그대로 잔존.

---

### 4. 서버 등록이 연결 검증 없이 바로 저장되고, 테스트 엔드포인트는 저장 이후에만 사용할 수 있다

관련 파일:
- `backend/routers/servers.py:157`
- `backend/routers/servers.py:266`
- `feature/collect.md:90`

문제:
- 설계 문서에는 "연결 테스트 성공 후 저장" 흐름이 적혀 있지만 현재 생성 API는 검증 없이 DB에 먼저 저장한다.
- 테스트 API도 `/{server_id}/test` 형태라 이미 저장된 서버만 검사할 수 있다.

영향:
- 잘못된 호스트/포트/credential이 그대로 등록되고, collector 실패 로그만 남긴다.
- 운영자가 쓰레기 엔트리를 직접 삭제해야 하는 흐름이 된다.

권고:
- 저장 전 payload 자체를 검사하는 테스트 엔드포인트를 별도로 두는 편이 맞다.
- 최소한 생성 시 1회 연결 검증 실패면 저장을 막아야 한다.

코드 검토 결과 (2026-03-21): `servers.py:157-186`에서 `POST /servers`는 admin 검증 후 즉시 DB 저장. `POST /servers/{server_id}/test`(`line:266`)는 이미 저장된 서버에만 동작하며 저장 전 dry-run endpoint 없음. `_sync_test_connection()` 구현 자체는 완성도 높으나 저장 전 호출 경로가 없어 문제 잔존.

---

### 5. Slack slash command 검증이 fail-open이다

관련 파일:
- `backend/routers/slack.py:23`
- `backend/routers/slack.py:118`

문제:
- `SLACK_SIGNING_SECRET`가 비어 있으면 `_verify_slack_signature()`는 요청 검증을 그냥 건너뛴다.
- `/slack/gpu`는 `SLACK_BOT_TOKEN`만 있으면 동작하므로, 토큰은 넣고 signing secret만 빠진 배포에서 인증 없는 endpoint가 된다.

영향:
- 외부 요청이 Slack slash command를 위장해 내부 상태를 조회할 수 있다.
- 설정 누락이 "안전하게 실패"하지 않고 "조용히 비보호 모드"로 들어간다.

권고:
- `SLACK_BOT_TOKEN`이 설정된 환경에서는 `SLACK_SIGNING_SECRET` 누락 시 endpoint를 비활성화하거나 500/503으로 fail-closed 해야 한다.

코드 검토 결과 (2026-03-21): `slack.py:23-28`에서 `signing_secret`이 falsy면 조건 없이 `return`. `slack.py:118-119`에서 bot_token 없을 때만 503 처리; signing_secret 누락은 체크 없음. fail-open 동작 그대로 잔존.

---

## Testing Gap

### 1. 자동 테스트가 없다

현재 특히 필요한 테스트:
- `psutil` 부재 시 시스템 메트릭 fallback 동작 검증
- note 인증이 첫 성공 시 조기 종료되는지 또는 timeout에 묶이지 않는지 검증
- credential 전환 시 이전 키/비밀번호가 실제로 제거되는지 검증
- Slack signing secret 누락 시 slash command가 fail-open 하지 않는지 검증

## 결론

현재 백엔드의 핵심 리스크는 원격 의존성(`psutil`), note 인증의 확장성, credential 갱신 semantics, Slack 서명 검증의 fail-open 동작이다.
2026-03-21 코드 직접 검토 결과, 위 5개 Open Findings 모두 원본 지적 그대로 잔존 확인. 해결된 항목 없음.
