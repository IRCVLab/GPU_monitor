# Storage View 구현 계획

## 목표

- 기존 Streamlit 모니터링의 스토리지 보기 기능을 `monitoring_v2` 대시보드에 옮긴다.
- 서버 카드 안에서 현재 스토리지 상태를 compact하게 확인할 수 있게 한다.
- 이번 범위는 **live status 조회**까지로 제한하고, 스토리지 히스토리 저장은 제외한다.

## 참고 구현

- 원본 수집 로직: `IRCV_server_monitoring/monitoring_core/collectors/storage.py`
- 원본 UI 흐름: `IRCV_server_monitoring/app.py` 의 `render_storage_tab()`

## 범위

### 포함

- SSH pull 기반 스토리지 수집
- 10분 TTL 캐시
- `/servers/status` 와 websocket `update` payload에 `storage` 추가
- 서버 카드 `시스템` 섹션의 expanded detail 안에 storage summary + mount list 표시
- GPU/system live cadence를 10초로 정렬

### 제외

- storage history DB 저장
- low disk alert/event 규칙
- NAS / NFS / CIFS 등 네트워크 파일시스템 표시
- directory-level drilldown, Docker/cache/Trash 분석

## 수집 규칙

- 로컬 물리 디스크 계열 마운트만 표시한다.
- 제외 대상:
  - `tmpfs`, `overlay`, `proc`, `sysfs` 등 가상 파일시스템
  - `nfs`, `cifs`, `smbfs`, `nfs4` 등 네트워크 파일시스템
  - `/nas`, `/mnt/nas` 등 NAS 계열 경로
  - 5GB 미만 파티션
- 출력 구조는 원본 Streamlit 구현의 `summary + mounts[]`를 그대로 따른다.

## API / 상태 계약

`ServerState` 에 `storage`를 별도 필드로 추가한다. `system` 안으로 합치지 않는다.

```ts
type StorageSummary = {
  mount_count: number;
  total: number;
  used: number;
  percent: number;
};

type StorageMount = {
  mount: string;
  device: string;
  fs_type: string;
  size: number;
  used: number;
  available: number;
  percent: number;
};

type StorageInfo = {
  collected_at: string | null;
  summary: StorageSummary;
  mounts: StorageMount[];
};
```

payload 예시:

```json
{
  "server_id": 1,
  "status": "online",
  "gpus": [],
  "system": {
    "cpu_percent": 18.3,
    "ram_used": 5200,
    "ram_total": 64000
  },
  "storage": {
    "collected_at": "2026-03-22T08:10:00+00:00",
    "summary": {
      "mount_count": 2,
      "total": 4012345678901,
      "used": 2156789012345,
      "percent": 53.7
    },
    "mounts": [
      {
        "mount": "/",
        "device": "/dev/nvme0n1p2",
        "fs_type": "ext4",
        "size": 1024000000000,
        "used": 512000000000,
        "available": 512000000000,
        "percent": 50.0
      }
    ]
  }
}
```

## 구현 방식

### 백엔드

- `backend/collectors/storage.py` 추가
  - 기존 Streamlit storage collector를 `monitoring_v2` SSH client에 맞게 이식
  - 10분 TTL 캐시 유지
- `backend/collectors/server_collector.py`
  - GPU/system 수집 루프를 10초 cadence로 유지
  - storage는 collector 내부 TTL 덕분에 10분 단위로만 실제 SSH 실행
  - storage 실패만으로 degraded 처리하지 않는다
  - `current_data` 와 websocket payload에 `storage` 포함
- `backend/collectors/manager.py`
  - `/servers/status` 응답에 `storage` 포함

### 프론트엔드

- `frontend/src/lib/types.ts`
  - `StorageSummary`, `StorageMount`, `StorageInfo` 추가
  - `ServerState.storage` 추가
- `frontend/src/lib/stores/servers.ts`
  - storage normalize / equality 비교 추가
- `frontend/src/lib/components/ServerCard.svelte`
  - `시스템` 헤더의 compact summary에는 `CPU / RAM / 총 GPU power`만 표시
  - 펼침 상태에서만 storage summary + mount별 usage row 표시
  - mount row는 `mount path / used-total / usage bar` 순으로 읽히게 배치

## UI 원칙

- 시스템 섹션 안에 넣어 서버 카드 구조를 늘리지 않는다.
- 접힌 상태에서는 `CPU / RAM / GPU power`만 보이고, 펼쳤을 때만 storage를 노출한다.
- Apple-like compact UI를 유지하기 위해 mount row는 dense한 1줄 요약 중심으로 구성한다.
- mount가 많아도 카드 높이가 과도하게 커지지 않도록 상위 몇 개만 먼저 보여주고 필요 시 스크롤 가능한 블록으로 처리한다.

## 리스크와 대응

- mount list가 많은 서버는 카드가 너무 길어질 수 있다.
  - percent 내림차순 정렬 + 높이 제한으로 제어
- storage를 `system` 안에 합치면 기존 비교 로직과 UI가 복잡해진다.
  - 별도 `storage` 필드로 분리
- 빠른 server last_seen 과 느린 storage snapshot 이 섞이면 stale data로 오해될 수 있다.
  - `storage.collected_at` 를 별도로 전달해 freshness를 분리한다.
- 10초 loop에서 매번 storage SSH 호출이 발생하면 불필요한 부하가 생긴다.
  - collector 내부 10분 TTL 캐시 유지

## 이번 구현 완료 조건

- 대시보드 첫 로드와 websocket 갱신 모두에서 storage가 보인다.
- storage가 없는 서버에서도 카드가 깨지지 않는다.
- GPU/system live cadence가 10초로 동작한다.
- storage는 10분 단위로만 새로 수집된다.
- `npm run check`, `npm run build`, `python -m compileall backend` 통과
