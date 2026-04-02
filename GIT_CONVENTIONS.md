# Git 커밋 컨벤션

## 원칙

- 기능 단위로 커밋한다. 파일 수가 많아도 논리적으로 묶이면 하나의 커밋.
- 작업이 끝나는 시점마다 커밋한다. 전체 작업을 한꺼번에 커밋하지 않는다.
- 커밋 메시지에 AI 서명(Co-Authored-By 등)을 남기지 않는다.

## 메시지 형식

```
<type>(<scope>): <한 줄 요약>

<본문>
- 무엇을 바꿨는지
- 왜 바꿨는지 (버그 수정이라면 어떤 증상이었는지, 기능 추가라면 왜 필요했는지)
- 어떻게 해결했는지 (비자명한 경우)
```

### type
- `feat`: 새 기능
- `fix`: 버그 수정
- `refactor`: 동작 변경 없는 코드 개선
- `style`: 스타일/CSS 변경
- `chore`: 설정, 빌드, 문서 등 코드 외 변경
- `docs`: 문서만 변경

### scope 예시
- `backend/logs`, `backend/notes`, `backend/servers`
- `frontend/gpubar`, `frontend/servercard`, `frontend/logs-page`
- `ui/tokens`, `ui/modal`

## 나쁜 예
```
fix stuff
update files
feat: add event log system, fix all review issues, improve UI  ← 너무 큰 범위
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>     ← 금지
```

## 좋은 예
```
feat(backend/logs): add event_logs table and GET /logs API

이벤트 로그 시스템 초기 구현.
- models.py에 EventLog 테이블 추가 (server_id, event_type, severity, message, metadata, created_at)
- event_logger.py: log_event() async 유틸 — 호출자 부담 없이 비동기 기록
- routers/logs.py: GET /logs (server_id/event_type/severity/limit/offset 필터),
  GET /logs/event-types (프론트 필터 드롭다운용 distinct 목록)
- main.py에 logs 라우터 등록 및 60초 주기 7일 초과 레코드 자동 정리 루프 추가
```
