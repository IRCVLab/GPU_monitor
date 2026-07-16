<script lang="ts">
  import '$lib/styles/monitor-cards.css';
  import type { ServerState, Note, ServerStatus } from '$lib/types';
  import { getNotes, deleteNote } from '$lib/api';
  import GpuBar from '$lib/components/GpuBar.svelte';
  import NoteForm from '$lib/components/NoteForm.svelte';
  import { getCompactGpuState } from '$lib/utils/compactGpuAvailability';
  import { classifyLoadRatio, classifyPressure, normalizeLoadRatio, pressureLabel } from '$lib/utils/resourcePressure';

  let {
    server,
    nowMs,
    onEdit,
    showNetwork = true
  }: {
    server: ServerState;
    nowMs: number;
    onEdit?: (server: ServerState) => void;
    showNetwork?: boolean;
  } = $props();

  let sysExpanded = $state(false);
  let notesExpanded = $state(false);
  let notes = $state<Note[]>([]);
  let notesLoaded = $state(false);
  let notesLoading = $state(false);
  let notesError = $state('');
  let notesServerId = $state<number | null>(null);

  let deleteState = $state<Record<number, { loading: boolean; error: string }>>({});
  let deletePassword = $state<Record<number, string>>({});

  const ONE_MINUTE_MS = 60 * 1000;
  const ONE_HOUR_MS = 60 * ONE_MINUTE_MS;
  const ONE_DAY_MS = 24 * ONE_HOUR_MS;
  const DEFAULT_SSH_PORT = 22;
  const FRESHNESS_WARNING_AFTER_MS = 30_000;

  type GpuHoldCue = { owner: string; remaining: string; memo: string };
  type OperationalState = 'healthy' | 'impaired';


  const statusConfig: Record<ServerStatus, { label: string }> = {
    online: { label: '정상' },
    offline: { label: '오프라인' },
    degraded: { label: '지연' },
    unknown: { label: '확인중' }
  };

  function formatStorage(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0B';

    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let value = bytes;
    let unitIndex = 0;

    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex += 1;
    }

    const digits = value >= 100 || unitIndex === 0 ? 0 : value >= 10 ? 1 : 2;
    return `${value.toFixed(digits)}${units[unitIndex]}`;
  }

  function formatFixed(value: number | null | undefined, digits = 1): string {
    return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '–';
  }

  function formatRoundedCount(value: number | null | undefined, allowZero = true): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '–';
    if (!allowZero && value <= 0) return '–';
    return `${Math.round(value)}`;
  }

  function absoluteTime(iso: string | null): string {
    if (!iso) return '업데이트 없음';

    return new Intl.DateTimeFormat('ko-KR', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }).format(new Date(iso));
  }

  function freshnessIssueText(iso: string | null, now: number): string {
    if (!iso) return '갱신 정보 없음';
    const timestamp = Date.parse(iso);
    if (Number.isNaN(timestamp)) return '갱신 시간 오류';

    const elapsedMs = Math.max(0, now - timestamp);
    if (elapsedMs < FRESHNESS_WARNING_AFTER_MS) return '';

    const elapsedSeconds = Math.floor(elapsedMs / 1000);
    if (elapsedSeconds < 60) return `마지막 갱신 ${elapsedSeconds}초 전`;

    const elapsedMinutes = Math.floor(elapsedSeconds / 60);
    if (elapsedMinutes < 60) return `마지막 갱신 ${elapsedMinutes}분 전`;

    const elapsedHours = Math.floor(elapsedMinutes / 60);
    if (elapsedHours < 24) return `마지막 갱신 ${elapsedHours}시간 전`;
    return `마지막 갱신 ${Math.floor(elapsedHours / 24)}일 전`;
  }


  function stateVeilLabelFor(status: ServerStatus, reasonCode: string | null): string {
    switch (reasonCode) {
      case 'gpu_device_missing':
        return 'GPU 인식 누락';
      case 'stale_snapshot':
      case 'dev-sim-stale':
        return '수집 지연';
      case 'stale_offline':
        return '수집 중단';
      case 'gpu_collect_failed':
        return 'GPU 메트릭 수집 실패';
      case 'system_collect_failed':
        return '시스템 메트릭 수집 실패';
      case 'unknown':
        return '상태 확인 중';
      case 'offline':
      case 'connect_failed':
      case 'connection_failed':
      case 'ssh_connect_failed':
      case 'dev-sim-offline':
        return 'SSH 연결 실패';
    }

    if (status === 'offline') return 'SSH 연결 실패';
    if (status === 'unknown') return '상태 확인 중';
    if (status === 'degraded') return '메트릭 수집 실패';
    return '수집 지연';
  }

  function secondaryVeilText(reason: string, age: string): string {
    return [reason, age].filter(Boolean).join(' · ');
  }

  function isHistoricalSystemTelemetryStatus(status: ServerStatus, reasonCode: string | null, refreshText: string): boolean {
    switch (reasonCode) {
      case 'stale_snapshot':
      case 'dev-sim-stale':
      case 'stale_offline':
      case 'system_collect_failed':
      case 'unknown':
      case 'offline':
      case 'connect_failed':
      case 'connection_failed':
      case 'ssh_connect_failed':
      case 'dev-sim-offline':
        return true;
    }

    return status === 'offline' || status === 'unknown' || Boolean(refreshText);
  }


  function parseNoteTime(iso: string | null): number | null {
    if (!iso) return null;
    const ms = Date.parse(iso);
    return Number.isNaN(ms) ? null : ms;
  }

  function noteVisible(note: Note): boolean {
    const expiresAtMs = parseNoteTime(note.expires_at);
    return expiresAtMs === null || expiresAtMs > nowMs;
  }

  function noteRemainingMs(note: Note): number | null {
    const expiresAtMs = parseNoteTime(note.expires_at);
    if (expiresAtMs === null) return null;
    return expiresAtMs - nowMs;
  }

  function noteRemainingText(note: Note): string {
    const remainingMs = noteRemainingMs(note);
    if (remainingMs === null) return '';
    if (remainingMs <= 0) return '만료됨';

    const seconds = Math.ceil(remainingMs / 1000);
    if (seconds < 60) return `${seconds}초`;

    const minutes = Math.ceil(seconds / 60);
    if (minutes < 60) return `${minutes}분`;

    const hours = Math.ceil(minutes / 60);
    if (hours < 48) return `${hours}시간`;

    const days = Math.ceil(hours / 24);
    return `${days}일`;
  }

  function noteExpiryTextClass(note: Note): string {
    const remainingMs = noteRemainingMs(note);
    if (remainingMs !== null && remainingMs <= ONE_HOUR_MS) return 'text-red-300/78';
    if (remainingMs !== null && remainingMs <= ONE_DAY_MS) return 'text-amber-200/80';
    return 'text-sky-200/70';
  }

  function holdGpuIndices(note: Note): number[] {
    return [...new Set(note.gpu_indices.filter((value) => Number.isInteger(value) && value >= 0))].sort((a, b) => a - b);
  }

  function notePreviewBadgeClass(note: Note): string {
    const remainingMs = noteRemainingMs(note);
    if (remainingMs !== null && remainingMs <= ONE_HOUR_MS) return 'is-urgent';
    if (remainingMs !== null && remainingMs <= ONE_DAY_MS) return 'is-soon';
    return 'is-fresh';
  }

  function noteDate(iso: string): string {
    const date = new Date(iso);
    return `${date.getFullYear()}.${String(date.getMonth() + 1).padStart(2, '0')}.${String(date.getDate()).padStart(2, '0')}`;
  }

  const statusMeta = $derived(statusConfig[server.status] ?? statusConfig.unknown);
  const refreshText = $derived(freshnessIssueText(server.last_seen, nowMs));
  const endpointText = $derived(
    server.port && server.port !== DEFAULT_SSH_PORT ? `${server.host}:${server.port}` : server.host
  );
  const statusReasonText = $derived(server.status_reason?.message ?? '');
  const statusReasonCode = $derived(server.status_reason?.code ?? null);
  const statusTooltip = $derived(statusReasonText ? `${statusMeta.label} · ${statusReasonText}` : statusMeta.label);
  const operationalState: OperationalState = $derived(server.status === 'online' && !refreshText ? 'healthy' : 'impaired');
  const stateVeilLabel = $derived(stateVeilLabelFor(server.status, refreshText ? statusReasonCode ?? 'stale_snapshot' : statusReasonCode));
  const stateVeilSecondary = $derived(secondaryVeilText(statusReasonText, refreshText));
  const isHistoricalSystemTelemetry = $derived(isHistoricalSystemTelemetryStatus(server.status, statusReasonCode, refreshText));
  const staleAvailabilityState = $derived('unknown');
  const availableGpuCount = $derived.by(() =>
    server.gpus.filter(
      (gpu) => (operationalState === 'impaired' ? staleAvailabilityState : getCompactGpuState(server.status, server.last_seen, gpu)) === 'available'
    ).length
  );
  const hasAvailableGpu = $derived(availableGpuCount > 0);

  const cpuPct = $derived(server.system?.cpu_percent ?? 0);
  const ramUsed = $derived(server.system ? (server.system.ram_used / 1024).toFixed(1) : '–');
  const ramTotal = $derived(server.system ? (server.system.ram_total / 1024).toFixed(1) : '–');
  const ramPct = $derived(
    server.system && server.system.ram_total > 0
      ? (server.system.ram_used / server.system.ram_total) * 100
      : 0
  );
  const storageSummary = $derived(server.storage?.summary ?? null);
  const storageUsedText = $derived(storageSummary ? formatStorage(storageSummary.used) : '–');
  const storageTotalText = $derived(storageSummary ? formatStorage(storageSummary.total) : '–');
  const storagePct = $derived(storageSummary?.percent ?? 0);
  const storageMounts = $derived(
    [...(server.storage?.mounts ?? [])].sort(
      (a, b) => b.percent - a.percent || a.mount.localeCompare(b.mount)
    )
  );
  const systemPreviewUnavailableText = '–';
  const loadAvg1 = $derived(server.system?.load_avg_1 ?? null);
  const loadAvg5 = $derived(server.system?.load_avg_5 ?? null);
  const loadAvg15 = $derived(server.system?.load_avg_15 ?? null);
  const cpuCount = $derived(server.system?.cpu_count ?? null);
  const cpuPressureSome = $derived(server.system?.cpu_pressure_some ?? null);
  const cpuRunningTasks = $derived(server.system?.cpu_running_tasks ?? null);
  const ioSome = $derived(server.system?.io_pressure_some ?? null);
  const ioFull = $derived(server.system?.io_pressure_full ?? null);
  const ioBlocked = $derived(server.system?.io_blocked_tasks ?? null);
  const loadRatio = $derived(normalizeLoadRatio(loadAvg1, cpuCount));
  const loadLevel = $derived(isHistoricalSystemTelemetry ? 'unknown' : classifyLoadRatio(loadRatio));
  const cpuPressureLevel = $derived(classifyPressure(cpuPressureSome));
  const ioPressureLevel = $derived.by(() => {
    const someLevel = classifyPressure(ioSome);
    const fullLevel = classifyPressure(ioFull);

    if (someLevel === 'bottleneck' || fullLevel === 'bottleneck') return 'bottleneck';
    if (someLevel === 'pressure' || fullLevel === 'pressure') return 'pressure';
    if ((ioBlocked ?? 0) > 0) return 'pressure';
    if (someLevel === 'idle' || fullLevel === 'idle') return 'idle';
    return 'unknown';
  });
  const loadPreviewPrefixText = $derived(isHistoricalSystemTelemetry && server.system ? '마지막 · ' : '');
  const loadPreviewText = $derived.by(() => {
    const loadText = formatFixed(loadAvg1);
    const cpuText = formatRoundedCount(cpuCount, false);
    if (loadText === systemPreviewUnavailableText && cpuText === systemPreviewUnavailableText) {
      return `${loadPreviewPrefixText}부하 – · CPU –`;
    }
    return `${loadPreviewPrefixText}부하 ${loadText} · CPU ${cpuText}`;
  });
  const cpuPressureLabelText = $derived(pressureLabel(cpuPressureLevel));
  const ioPressureLabelText = $derived(pressureLabel(ioPressureLevel));
  const cpuPressureCauseLabel = $derived(
    cpuPressureLevel === 'pressure' ? 'CPU 압박' : cpuPressureLevel === 'bottleneck' ? 'CPU 병목' : null
  );
  const ioPressureCauseLabel = $derived(
    ioPressureLevel === 'pressure' ? 'I/O 압박' : ioPressureLevel === 'bottleneck' ? 'I/O 병목' : null
  );
  const loadPreviewCauses = $derived.by(() => {
    if (isHistoricalSystemTelemetry) return [];

    const causes = [];
    if (cpuPressureCauseLabel) {
      causes.push({ key: 'cpu', label: cpuPressureCauseLabel, level: cpuPressureLevel, detail: cpuPressureLabelText });
    }
    if (ioPressureCauseLabel) {
      causes.push({ key: 'io', label: ioPressureCauseLabel, level: ioPressureLevel, detail: ioPressureLabelText });
    }
    return causes;
  });
  const loadDetailText = $derived(`${formatFixed(loadAvg1)} · ${formatFixed(loadAvg5)} · ${formatFixed(loadAvg15)}`);
  const cpuCountText = $derived(formatRoundedCount(cpuCount, false));
  const cpuSystemDetailText = $derived(server.system ? `${cpuPct.toFixed(0)}%` : systemPreviewUnavailableText);
  const cpuSystemMetaText = $derived(server.system ? `논리 CPU ${cpuCountText}` : systemPreviewUnavailableText);
  const ramSystemDetailText = $derived(server.system ? `${ramPct.toFixed(0)}%` : systemPreviewUnavailableText);
  const ramCapacityText = $derived(server.system ? `${ramUsed}/${ramTotal} GB` : systemPreviewUnavailableText);
  const storageSystemDetailText = $derived(storageSummary ? `${storagePct.toFixed(0)}%` : systemPreviewUnavailableText);
  const storageCapacityText = $derived(storageSummary ? `${storageUsedText}/${storageTotalText}` : systemPreviewUnavailableText);
  const cpuRunningText = $derived(formatRoundedCount(cpuRunningTasks));
  const cpuPreviewText = $derived(isHistoricalSystemTelemetry ? systemPreviewUnavailableText : cpuSystemDetailText);
  const ramPreviewText = $derived(
    isHistoricalSystemTelemetry || !server.system ? systemPreviewUnavailableText : `${ramPct.toFixed(0)}%`
  );
  const storagePreviewText = $derived(
    isHistoricalSystemTelemetry || !storageSummary ? systemPreviewUnavailableText : `${storagePct.toFixed(0)}%`
  );
  const cpuPressureSomeText = $derived(cpuPressureSome !== null ? `${cpuPressureSome.toFixed(1)}%` : systemPreviewUnavailableText);
  const ioSomeText = $derived(ioSome !== null ? `${ioSome.toFixed(1)}%` : systemPreviewUnavailableText);
  const ioFullText = $derived(ioFull !== null ? `${ioFull.toFixed(1)}%` : systemPreviewUnavailableText);
  const ioBlockedText = $derived(formatRoundedCount(ioBlocked));
  const loadAverageHelpText = 'Load avg는 실행 가능하거나 I/O 대기 중인 작업 수의 1·5·15분 평균입니다. 논리 CPU 수는 포화 판단 기준이며 최댓값이 아닙니다.';
  const ioPressureHelpText = `${loadAverageHelpText} · PSI는 최근 10초 동안 작업이 CPU 또는 I/O 때문에 대기한 시간의 비율입니다.`;
  const hasSystemSection = $derived(Boolean(server.system || server.storage || server.gpus.length > 0));

  const visibleNotes = $derived.by(() => notes.filter((note) => noteVisible(note)));
  const activeHoldNotesByGpu = $derived.by(() => {
    const holds: Record<number, GpuHoldCue[]> = {};

    for (const note of notes) {
      if (note.kind !== 'hold' || !noteVisible(note)) continue;

      const gpuIndices = holdGpuIndices(note);
      if (gpuIndices.length === 0) continue;

      const cue: GpuHoldCue = {
        owner: note.username,
        remaining: noteRemainingText(note),
        memo: note.content
      };

      for (const gpuIndex of gpuIndices) {
        holds[gpuIndex] = [...(holds[gpuIndex] ?? []), cue];
      }
    }

    return holds;
  });
  const previewNotes = $derived(visibleNotes.slice(0, 1));

  async function loadNotes(force = false) {
    if (notesLoading) return;
    if (notesLoaded && !force) return;

    notesLoading = true;
    try {
      const loadedNotes = await getNotes(server.server_id);
      notes = [...loadedNotes].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      notesLoaded = true;
      notesError = '';
    } catch (error) {
      notesError = error instanceof Error ? error.message : '메모를 불러오지 못했습니다.';
    } finally {
      notesLoading = false;
    }
  }

  function toggleNotes() {
    notesExpanded = !notesExpanded;
    if (notesExpanded) {
      void loadNotes();
    }
  }

  function onNoteCreated(note: Note) {
    notes = [note, ...notes.filter((existing) => existing.id !== note.id)];
    notesLoaded = true;
  }

  async function handleDelete(note: Note) {
    const password = deletePassword[note.id] ?? '';
    if (!password.trim()) return;

    deleteState = { ...deleteState, [note.id]: { loading: true, error: '' } };
    try {
      await deleteNote(server.server_id, note.id, note.username, password);
      notes = notes.filter((existing) => existing.id !== note.id);
      const { [note.id]: _removed, ...rest } = deleteState;
      deleteState = rest;
    } catch (error) {
      deleteState = {
        ...deleteState,
        [note.id]: {
          loading: false,
          error: error instanceof Error ? error.message : '삭제 실패'
        }
      };
    }
  }

  $effect(() => {
    const serverId = server.server_id;
    if (notesServerId === serverId) return;

    notesServerId = serverId;
    sysExpanded = false;
    notesExpanded = false;
    notes = [];
    notesLoaded = false;
    notesLoading = false;
    notesError = '';
    deleteState = {};
    deletePassword = {};

    if (serverId) {
      void loadNotes();
    }
  });

</script>

<article class="monitor-card bg-surface-card border border-surface-border" data-status={server.status} data-operational-state={operationalState} data-network={showNetwork ? server.network : undefined} data-has-available={hasAvailableGpu ? 'true' : 'false'}>
  <header class="monitor-card__header">
    <div class="monitor-card__title-row">
      <div class="monitor-card__title-line">
        <h2 class="monitor-card__title">{server.server_name}</h2>
        <span class="monitor-card__status" data-status={server.status} title={statusTooltip}>
          <span class="monitor-card__status-dot" aria-hidden="true"></span>
          {#if statusMeta.label === '정상'}
            <span class="monitor-card__status-text monitor-card__sr-only">{statusMeta.label}</span>
          {:else}
            <span class="monitor-card__status-text">{statusMeta.label}</span>
          {/if}
        </span>
        <span class="monitor-card__host" title={server.port ? `${server.host}:${server.port}` : server.host}>{endpointText}</span>
      </div>

      {#if onEdit}
        <button
          onclick={() => onEdit(server)}
          class="monitor-card__edit-button"
          aria-label="서버 편집"
          title="서버 편집"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="monitor-card__edit-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
      {/if}
    </div>

  </header>

  <div class="monitor-card__body">
    {#if server.gpus.length > 0}
      <div class="monitor-card__gpu-list">
        {#each server.gpus as gpu (gpu.index)}
          <GpuBar
            {gpu}
            state={operationalState === 'impaired' ? staleAvailabilityState : getCompactGpuState(server.status, server.last_seen, gpu)}
            advisoryHolds={activeHoldNotesByGpu[gpu.index] ?? []}
          />
        {/each}
      </div>
    {/if}

    <div class="monitor-card__footer">
    {#if hasSystemSection}
      <section class="monitor-card__footer-section">
        <button
          onclick={() => (sysExpanded = !sysExpanded)}
          class="monitor-card__footer-toggle"
          aria-expanded={sysExpanded}
          aria-controls={`system-panel-${server.server_id}`}
          aria-describedby={`system-load-help-${server.server_id}`}
        >
          <span class="monitor-card__footer-toggle-main">
            <span class="monitor-card__footer-label">시스템</span>
          </span>
          <span class="monitor-card__footer-side">
            {#if !sysExpanded}
              <span class="monitor-card__footer-preview monitor-card__system-preview">
                <span class="monitor-card__system-inline-metric">CPU {cpuPreviewText}</span>
                <span class="monitor-card__system-inline-metric">RAM {ramPreviewText}</span>
                <span class="monitor-card__system-inline-metric">Storage {storagePreviewText}</span>
                <span class="monitor-card__load-preview" title={loadAverageHelpText} data-level={loadLevel}>
                  <span class="monitor-card__load-text">{loadPreviewText}</span>
                </span>
                {#each loadPreviewCauses as cause}
                  <span class="monitor-card__pressure-cause" data-level={cause.level}>· {cause.label}</span>
                {/each}
              </span>
            {/if}
            <span id={`system-load-help-${server.server_id}`} class="monitor-card__sr-only">{loadAverageHelpText}</span>
            <span class="monitor-card__footer-disclosure" class:is-expanded={sysExpanded} aria-hidden="true"></span>
          </span>
        </button>

        <div
          id={`system-panel-${server.server_id}`}
          class="monitor-card__disclosure-shell"
          data-expanded={sysExpanded ? 'true' : 'false'}
          aria-hidden={!sysExpanded}
          inert={!sysExpanded}
        >
          <div class="monitor-card__disclosure-inner monitor-card__footer-panel">
            {#if isHistoricalSystemTelemetry && server.system}
              <span class="monitor-card__last-sample-label">마지막 수집값</span>
            {/if}

            <div class="monitor-card__resource-overview" role="group" aria-label="호스트 리소스">
              <span class="monitor-card__resource-item" data-resource="cpu" data-level={cpuPct >= 90 ? 'high' : cpuPct >= 75 ? 'medium' : 'normal'}>
                <small>CPU</small>
                <strong class="monitor-card__resource-value">{cpuSystemDetailText}</strong>
                <span class="monitor-card__resource-meta" title={cpuSystemMetaText}>{cpuSystemMetaText}</span>
              </span>
              <span class="monitor-card__resource-item" data-resource="ram" data-level={ramPct >= 90 ? 'high' : ramPct >= 75 ? 'medium' : 'normal'}>
                <small>RAM</small>
                <strong class="monitor-card__resource-value">{ramSystemDetailText}</strong>
                <span class="monitor-card__resource-meta" title={ramCapacityText}>{ramCapacityText}</span>
              </span>
              <span class="monitor-card__resource-item" data-resource="storage" data-level={storagePct >= 90 ? 'high' : storagePct >= 75 ? 'medium' : 'normal'}>
                <small>Storage</small>
                <strong class="monitor-card__resource-value">{storageSystemDetailText}</strong>
                <span class="monitor-card__resource-meta" title={storageCapacityText}>{storageCapacityText}</span>
              </span>
            </div>

            <div class="monitor-card__pressure-table" title={ioPressureHelpText}>
              <div class="monitor-card__pressure-row">
                <span class="monitor-card__pressure-label">Load avg</span>
                <span class="monitor-card__pressure-values monitor-card__pressure-values--load">
                  <span class="monitor-card__pressure-datum monitor-card__pressure-datum--load">
                    <small>1 · 5 · 15m</small><strong>{loadDetailText}</strong>
                  </span>
                  <span class="monitor-card__pressure-datum">
                    <small>실행 중</small><strong>{cpuRunningText}</strong>
                  </span>
                </span>
              </div>
              <div class="monitor-card__pressure-row">
                <span class="monitor-card__pressure-label">병목 단서</span>
                <span class="monitor-card__pressure-values monitor-card__pressure-values--clues">
                  <span class="monitor-card__pressure-datum"><small>CPU PSI</small><strong>{cpuPressureSomeText}</strong></span>
                  <span class="monitor-card__pressure-datum"><small>I/O some</small><strong>{ioSomeText}</strong></span>
                  <span class="monitor-card__pressure-datum"><small>I/O full</small><strong>{ioFullText}</strong></span>
                  <span class="monitor-card__pressure-datum"><small>blocked</small><strong>{ioBlockedText}</strong></span>
                </span>
              </div>
            </div>

            {#if server.storage}
              <div class="monitor-card__subsection">
                <div class="monitor-card__storage-head">
                  <div class="monitor-card__storage-summary">
                    <span class="monitor-card__subheading">Storage</span>
                    <span class="monitor-card__metric-value">{storageUsedText}/{storageTotalText}</span>
                    <span class="monitor-card__storage-meta">{server.storage.summary.mount_count} mounts</span>
                  </div>
                  {#if server.storage.collected_at}
                    <span class="monitor-card__storage-time">{absoluteTime(server.storage.collected_at)}</span>
                  {/if}
                </div>

                <div class="monitor-card__metric-row monitor-card__metric-row--storage">
                  <span class="monitor-card__metric-name">Disk</span>
                  <div class="monitor-meter" aria-hidden="true">
                    <div class="monitor-meter__fill monitor-meter__fill--storage" style={`width: ${Math.min(100, storagePct)}%`}></div>
                  </div>
                  <span class="monitor-card__metric-value">{storagePct.toFixed(0)}%</span>
                </div>

                {#if storageMounts.length > 0}
                  <div class="monitor-card__mount-list">
                    {#each storageMounts as mount}
                      <div class="monitor-card__mount-item">
                        <span class="monitor-card__mount-path" title={mount.mount}>{mount.mount}</span>
                        <span class="monitor-card__mount-usage">{formatStorage(mount.used)}/{formatStorage(mount.size)}</span>
                        <span class="monitor-card__mount-percent" data-level={mount.percent >= 90 ? 'high' : mount.percent >= 75 ? 'medium' : 'normal'}>
                          {mount.percent.toFixed(0)}%
                        </span>
                      </div>
                    {/each}
                  </div>
                {/if}
              </div>
            {/if}

            {#if server.gpus.length > 0}
              <div class="monitor-card__subsection monitor-card__subsection--hardware">
                <div class="monitor-card__subheading">GPU 하드웨어</div>
                <div class="monitor-card__hardware-grid">
                  {#each server.gpus as gpu (gpu.index)}
                    <div class="monitor-card__hardware-item">
                      <span class="monitor-card__hardware-index">G{gpu.index}</span>
                      <span class="monitor-card__hardware-value">{gpu.temperature}°C</span>
                      <span class="monitor-card__hardware-value">{Math.round(gpu.power_draw)}W</span>
                    </div>
                  {/each}
                </div>
              </div>
            {/if}
          </div>
        </div>
      </section>
    {/if}

    <section class="monitor-card__footer-section">
      <button
        onclick={toggleNotes}
        class="monitor-card__footer-toggle"
        aria-expanded={notesExpanded}
        aria-controls={`notes-panel-${server.server_id}`}
      >
        <span class="monitor-card__footer-toggle-main">
          <span class="monitor-card__footer-label">메모</span>
        </span>
        <span class="monitor-card__footer-side">
          {#if !notesExpanded}
            <span class="monitor-card__footer-preview monitor-card__footer-preview--notes">
              {#if previewNotes.length > 0}
                <span class="monitor-card__note-preview-main">
                  {#if previewNotes[0].kind === 'hold'}
                    <span class="monitor-card__note-preview-hold">
                      HOLD {#each holdGpuIndices(previewNotes[0]) as gpuIndex, index (gpuIndex)}{index > 0 ? '·' : ''}G{gpuIndex}{/each}
                    </span>
                  {/if}
                  <span class="monitor-card__note-preview-user">@{previewNotes[0].username}</span>
                  <span class="monitor-card__note-preview-content" title={previewNotes[0].content}>{previewNotes[0].content}</span>
                </span>
                {#if previewNotes[0].expires_at}
                  <span class={`monitor-card__note-preview-expiry ${notePreviewBadgeClass(previewNotes[0])}`}>
                    {noteRemainingText(previewNotes[0])}
                  </span>
                {/if}
              {:else if notesLoaded}
                <span class="monitor-card__footer-placeholder">메모 없음</span>
              {/if}
            </span>
          {/if}
          <span class="monitor-card__footer-disclosure" class:is-expanded={notesExpanded} aria-hidden="true"></span>
        </span>
      </button>

      <div
        id={`notes-panel-${server.server_id}`}
        class="monitor-card__disclosure-shell"
        data-expanded={notesExpanded ? 'true' : 'false'}
        aria-hidden={!notesExpanded}
        inert={!notesExpanded}
      >
        <div class="monitor-card__disclosure-inner monitor-card__footer-panel">
          {#if notesLoading}
            <div class="monitor-card__loading-state">
              <span class="inline-block h-3 w-3 animate-spin rounded-full border border-white/20 border-t-white/60"></span>
              불러오는 중...
            </div>
          {:else if notesError}
            <div class="monitor-card__error-row">
              <span class="monitor-card__error-text">{notesError}</span>
              <button onclick={() => void loadNotes(true)} class="monitor-card__retry-button">다시 시도</button>
            </div>
          {:else}
            <div class="monitor-card__memo-stack">
              <div class="monitor-card__memo-group monitor-card__memo-group--history">
                <div class="monitor-card__memo-group-head">
                  <span class="monitor-card__memo-group-title">기록</span>
                  <span class="monitor-card__memo-group-meta">{visibleNotes.length}</span>
                </div>

                {#if visibleNotes.length > 0}
                  <div class="monitor-note-list">
                    {#each visibleNotes as note (note.id)}
                      <div class="monitor-note-item">
                        <div class="monitor-note-item__body">
                          <div class="monitor-note-item__meta">
                            <span class="monitor-note-item__user">@{note.username}</span>
                            <span class="monitor-note-item__time" title={absoluteTime(note.created_at)}>{noteDate(note.created_at)}</span>
                            {#if note.expires_at}
                              <span class="monitor-card__separator" aria-hidden="true">·</span>
                              <span class={`monitor-note-item__expiry ${noteExpiryTextClass(note)}`}>
                                {noteRemainingText(note)}
                              </span>
                            {/if}
                          </div>
                          {#if note.kind === 'hold'}
                            <div class="monitor-note-item__hold">
                              <span class="monitor-note-item__kind">HOLD</span>
                              <span class="monitor-note-item__gpu-chips" aria-label="Selected GPUs">
                                {#each holdGpuIndices(note) as gpuIndex (gpuIndex)}
                                  <span class="monitor-note-item__gpu-chip">G{gpuIndex}</span>
                                {/each}
                              </span>
                            </div>
                          {/if}
                          <p class="monitor-note-item__content">{note.content}</p>
                          {#if deleteState[note.id]?.error}
                            <p class="monitor-card__error-text">{deleteState[note.id].error}</p>
                          {/if}
                        </div>

                        <div class="monitor-note-item__actions">
                          <input
                            type="password"
                            placeholder="pw"
                            bind:value={deletePassword[note.id]}
                            class="monitor-note-item__password"
                          />
                          <button
                            onclick={() => handleDelete(note)}
                            disabled={deleteState[note.id]?.loading}
                            class="monitor-note-item__delete"
                          >
                            {#if deleteState[note.id]?.loading}
                              <span class="inline-block h-3 w-3 animate-spin rounded-full border border-white/20 border-t-white/60"></span>
                            {:else}
                              삭제
                            {/if}
                          </button>
                        </div>
                      </div>
                    {/each}
                  </div>
                {:else}
                  <p class="monitor-card__memo-empty">아직 등록된 메모가 없습니다.</p>
                {/if}
              </div>

              <div class="monitor-card__memo-group monitor-card__memo-group--composer">
                <div class="monitor-card__memo-group-head">
                  <span class="monitor-card__memo-group-title">작성</span>
                  <span class="monitor-card__memo-group-meta">GPU 선택 시 HOLD</span>
                </div>
                <NoteForm
                  serverId={server.server_id}
                  gpus={server.gpus}
                  serverStatus={server.status}
                  lastSeen={server.last_seen}
                  active={notesExpanded}
                  onCreated={onNoteCreated}
                />
              </div>
            </div>
          {/if}
        </div>
      </div>
    </section>
    </div>
  </div>

  {#if operationalState === 'impaired'}
    <div class="monitor-card__state-veil" aria-hidden="true">
      <span class="monitor-card__state-veil-label">{stateVeilLabel}</span>
      {#if stateVeilSecondary}
        <span class="monitor-card__state-veil-secondary">{stateVeilSecondary}</span>
      {/if}
    </div>
  {/if}
</article>
