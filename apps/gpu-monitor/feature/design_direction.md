# Monitoring v2 UI Direction

작성일: 2026-03-22

목표:
- Apple-like but minimal
- compact density without cramped feeling
- refresh state must be truthful
- light mode must stay readable
- dashboard and logs must share one interaction language

## 1. Core Principles

- 정보는 많이 보여주되, 한 번에 강조하는 것은 적게 유지한다.
- 장식보다 상태를 우선한다. `Live` 같은 표현은 예쁘기보다 정확해야 한다.
- 카드 안에서는 `이름 -> GPU 상태 -> 보조 정보` 순서가 흔들리면 안 된다.
- 라이트 모드는 다크 모드의 반전이 아니라 별도 surface hierarchy로 설계한다.
- 그래픽 요소는 점, 얇은 tint, 미세한 blur 정도만 허용한다.

## 2. Dashboard Header

- 헤더는 페이지 전체 상태를 대표한다. 카드별 시간 정보와 경쟁하면 안 된다.
- 갱신 semantics는 한 줄로 통합한다.
  - 정상: `최근 전체 갱신: 3초 전 · 5초 자동 갱신`
  - 진행 중: `지금 갱신 중`
  - 실패/지연: `마지막 성공 갱신: 42초 전 · 갱신 지연`
- `Live`는 작은 connection signal로만 쓴다.
  - 작은 dot + 짧은 텍스트
  - 별도 큰 pill, 과한 gradient, 강한 glow 금지
- 타이틀 블록은 title과 meta를 한 덩어리로 묶되, title이 항상 가장 강한 대비를 가져야 한다.

구현 기준:
- title: 가장 진한 text
- meta line: title보다 한 단계 낮은 대비
- live signal: meta line 안의 보조 요소

## 3. Card Hierarchy

- 서버 카드는 `서버 이름`, `GPU rows`, `시스템/메모 토글` 3층 구조로 읽혀야 한다.
- network pill, host, timestamp는 secondary metadata로 낮춘다.
- GPU rows가 카드의 시각적 중심이어야 한다.
- collapsed row들은 "section title"이 아니라 "compact summary row"처럼 보여야 한다.

구현 기준:
- header padding은 짧게 유지
- title > GPU content > host/time > pills 순서로 대비를 배치
- `시스템`, `메모` 토글은 같은 높이, 같은 hover, 같은 chevron 규칙 사용

## 4. GPU Rows

- GPU row는 dense 해야 하지만 user readability를 해치면 안 된다.
- active GPU label과 GPU user는 accent color를 공유한다.
- idle은 훨씬 희미하게 처리한다.
- utilization/memory bar는 정보 보조 역할만 하고, 텍스트보다 더 튀지 않게 한다.

구현 기준:
- active label/user: blue-sky 계열
- idle text: low-contrast neutral
- row spacing은 촘촘하게, user text는 읽을 수 있도록 wrap 허용

## 5. System and Memo Collapse

- collapsed `시스템`은 한 줄 요약만 보여준다.
  - 예: `CPU 34% · RAM 12.4/24GB`
- expanded GPU hardware는 chip 묶음보다 dense mini-spec row가 낫다.
  - 예: `G0 72°C 180W`
- collapsed `메모`는 최신 메모 1개 preview만 보여준다.
  - count, author, short preview
  - 날짜나 추가 액션은 접힌 상태에서 숨긴다

구현 기준:
- collapsed summary는 한 줄
- expanded details만 별도 surface 위에 노출
- preview는 compact하지만 읽을 수 있어야 하며, 장식보다 내용이 먼저 보여야 한다

## 6. Light Mode

- 라이트 모드는 opacity remap 하나로 해결하지 않는다.
- 최소 3개 surface tier를 분리한다.
  - page background
  - header surface
  - card surface
- 카드가 가장 읽기 쉬운 밝은 surface여야 하고, header는 더 얇은 frosted surface로 둔다.
- muted text는 흐릿해 보이기만 하는 수준까지 내리지 않는다.

구현 기준:
- page: 가장 조용한 배경
- header: 얇은 blur + 약한 border
- card: 가장 선명한 읽기 surface
- muted text도 body background 위에서 즉시 읽혀야 한다

## 7. Logs Hierarchy

- 로그는 `severity / content / time` 3-zone 구조를 유지한다.
- message가 1차 정보다.
- server name은 anchor, event type은 tertiary label, time은 quiet metadata다.
- row 전체가 clickable이면 disclosure affordance가 반드시 보여야 한다.
- expanded metadata는 한 단계 들어간 surface 위에 보여준다.

구현 기준:
- severity는 badge + row edge cue로 표현
- message는 가장 큰 가독성 우선
- time은 조용하지만 정렬 가능해야 함
- expanded state는 row 배경과 disclosure 변화로 즉시 구분 가능해야 함

## 8. Subtle Graphics

- 허용되는 graphic/live 요소:
  - small live dot
  - very soft gradient tint
  - restrained shadow
  - thin border
- 금지되는 방향:
  - 큰 glow
  - 강한 glassmorphism
  - 장식이 상태보다 먼저 보이는 pill

## 9. Review Checklist

- header가 실제 refresh 상태를 정직하게 보여주는가
- title/meta/live가 하나의 compact hierarchy로 읽히는가
- 카드에서 GPU 정보가 항상 가장 먼저 보이는가
- collapsed system/memo가 summary row처럼 충분히 얇은가
- light mode에서 page/header/card가 서로 다른 surface로 읽히는가
- 로그 row가 열지 않아도 빠르게 스캔되는가
- graphics가 상태 전달을 돕고 있으며 장식으로 앞서지 않는가
