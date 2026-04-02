# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## 프로젝트 컨텍스트: GPU 모니터링 v3

이 프로젝트의 설계 결정사항과 배경을 숙지하고 코드를 작성할 것.
세부 내용은 `feature/` 디렉토리의 문서 참고.

### 핵심 결정사항

**수집 방식: SSH pull (에이전트 없음)**
모니터링 서버에서 아웃바운드 SSH로 수집. 내부망/외부망 모두 동일 방식.
에이전트 기반 push 방식 제안 금지 — 서버 등록 마찰이 큰 이유로 제외됨.

**기술 스택 고정**
- Backend: FastAPI + SQLite (PostgreSQL 제안 금지)
- Frontend: SvelteKit (Next.js/React 제안 금지)
- 실시간: WebSocket (polling 방식 제안 금지)

**기능 범위 엄수**
`PLAN.md`의 "제외" 목록에 있는 기능(Notion, LLM 분석, DnD 정렬, 유저 그룹 등)은
요청 없이 추가하지 말 것.

**인증: 계정 시스템 없음**
로그인/JWT/세션 없음. 쓰기 작업(메모, 서버 등록)만 인라인 패스워드 입력.
JWT, 회원가입, 로그인 endpoint 제안 금지. 상세 설계는 `feature/auth.md` 참고.

**Slack: HTTP mode만 사용**
Socket Mode 금지. FastAPI endpoint에 통합.
상세 설계는 `feature/slack.md` 참고.

**SSH 크리덴셜 보안**
- DB에는 반드시 암호화 저장 (Fernet)
- API 응답에 평문 크리덴셜 절대 포함 금지
- `has_password`, `has_key` 불리언만 반환

### 코드 작성 기준

- 파일 하나가 300줄 넘어가면 분리를 고려
- SQL은 항상 파라미터화 (f-string으로 쿼리 조합 금지)
- 환경변수는 `.env` + python-dotenv로 관리, 코드에 하드코딩 금지
- 에러 메시지는 사용자에게 SSH 접속 실패 / 인증 실패를 구분하여 반환
