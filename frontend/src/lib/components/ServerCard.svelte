<script lang="ts">
  import '$lib/styles/monitor-cards.css';
  import type { ServerState, Note, ServerStatus } from '$lib/types';
  import { getNotes, deleteNote } from '$lib/api';
  import GpuBar from '$lib/components/GpuBar.svelte';
  import NoteForm from '$lib/components/NoteForm.svelte';
  import { getCompactGpuState } from '$lib/utils/compactGpuAvailability';

  let {
    server,
    onEdit,
    showNetwork = true
  }: {
    server: ServerState;
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
  let noteNowMs = $state(Date.now());

  let deleteState = $state<Record<number, { loading: boolean; error: string }>>({});
  let deletePassword = $state<Record<number, string>>({});

  const ONE_MINUTE_MS = 60 * 1000;
  const ONE_HOUR_MS = 60 * ONE_MINUTE_MS;
  const ONE_DAY_MS = 24 * ONE_HOUR_MS;

  type GpuHoldCue = { owner: string; remaining: string; memo: string };
  type MetricLevel = 'normal' | 'medium' | 'high';

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

  function absoluteTime(iso: string | null): string {
    if (!iso) return '업데이트 없음';

    return new Intl.DateTimeFormat('ko-KR', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }).format(new Date(iso));
  }

  function metricLevel(percent: number | null | undefined): MetricLevel {
    if (!Number.isFinite(percent)) return 'normal';
    if ((percent ?? 0) >= 90) return 'high';
    if ((percent ?? 0) >= 75) return 'medium';
    return 'normal';
  }

  function parseNoteTime(iso: string | null): number | null {
    if (!iso) return null;
    const ms = Date.parse(iso);
    return Number.isNaN(ms) ? null : ms;
  }

  function noteVisible(note: Note): boolean {
    const expiresAtMs = parseNoteTime(note.expires_at);
    return expiresAtMs === null || expiresAtMs > noteNowMs;
  }

  function noteRemainingMs(note: Note): number | null {
    const expiresAtMs = parseNoteTime(note.expires_at);
    if (expiresAtMs === null) return null;
    return expiresAtMs - noteNowMs;
  }

  function noteRemainingText(note: Note): string {
    const remainingMs = noteRemainingMs(note);
    if (remainingMs === null) return '';
    if (remainingMs <= 0) return '곧 만료';

    const seconds = Math.ceil(remainingMs / 1000);
    if (seconds < 60) return `${seconds}초 남음`;

    const minutes = Math.ceil(seconds / 60);
    if (minutes < 60) return `${minutes}분 남음`;

    const hours = Math.ceil(minutes / 60);
    if (hours < 48) return `${hours}시간 남음`;

    const days = Math.ceil(hours / 24);
    return `${days}일 남음`;
  }

  function noteExpiryTextClass(note: Note): string {
    const remainingMs = noteRemainingMs(note);
    if (remainingMs !== null && remainingMs <= ONE_HOUR_MS) return 'text-red-300/78';
    if (remainingMs !== null && remainingMs <= ONE_DAY_MS) return 'text-amber-200/80';
    return 'text-sky-200/70';
  }

  function notePreviewCountdownText(note: Note): string {
    const remainingMs = noteRemainingMs(note);
    if (remainingMs === null) return '';
    if (remainingMs <= 0) return 'NOW';

    const totalMinutes = Math.ceil(remainingMs / ONE_MINUTE_MS);
    const totalHours = Math.ceil(remainingMs / ONE_HOUR_MS);
    const totalDays = Math.floor(remainingMs / ONE_DAY_MS);

    if (remainingMs >= ONE_DAY_MS) {
      const hours = Math.floor((remainingMs - totalDays * ONE_DAY_MS) / ONE_HOUR_MS);
      return hours > 0 ? `${totalDays}D ${hours}H` : `${Math.max(1, totalDays)}D`;
    }

    if (remainingMs >= ONE_HOUR_MS) {
      return `${totalHours}H`;
    }

    if (remainingMs >= ONE_MINUTE_MS) {
      return `${totalMinutes}M`;
    }

    const totalSeconds = Math.ceil(remainingMs / 1000);
    return `${totalSeconds}S`;
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
  const lastSeenAbsoluteText = $derived(absoluteTime(server.last_seen));
  const refreshText = $derived(`갱신 ${lastSeenAbsoluteText}`);
  const hostText = $derived(server.port ? `${server.host}:${server.port}` : server.host);
  const statusReasonText = $derived(server.status_reason?.message ?? '');
  const statusTooltip = $derived(statusReasonText ? `${statusMeta.label} · ${statusReasonText}` : statusMeta.label);

  const cpuPct = $derived(server.system?.cpu_percent ?? 0);
  const ramUsed = $derived(server.system ? (server.system.ram_used / 1024).toFixed(1) : '–');
  const ramTotal = $derived(server.system ? (server.system.ram_total / 1024).toFixed(1) : '–');
  const ramPct = $derived(
    server.system && server.system.ram_total > 0
      ? (server.system.ram_used / server.system.ram_total) * 100
      : 0
  );

  const totalGpuPower = $derived(
    server.gpus.reduce((sum, gpu) => sum + Math.max(0, gpu.power_draw || 0), 0)
  );
  const totalGpuPowerText = $derived(server.gpus.length > 0 ? `${Math.round(totalGpuPower)}W` : '–');
  const storageSummary = $derived(server.storage?.summary ?? null);
  const storageUsedText = $derived(storageSummary ? formatStorage(storageSummary.used) : '–');
  const storageTotalText = $derived(storageSummary ? formatStorage(storageSummary.total) : '–');
  const storagePct = $derived(storageSummary?.percent ?? 0);
  const storageMounts = $derived(
    [...(server.storage?.mounts ?? [])].sort(
      (a, b) => b.percent - a.percent || a.mount.localeCompare(b.mount)
    )
  );
  const cpuPreviewText = $derived(server.system ? `${cpuPct.toFixed(0)}%` : '–');
  const ramPreviewText = $derived(server.system ? `${ramUsed}/${ramTotal}GB` : '–');
  const diskPreviewText = $derived(storageSummary ? `${storagePct.toFixed(0)}%` : '–');
  const cpuLevel = $derived(server.system ? metricLevel(cpuPct) : 'normal');
  const ramLevel = $derived(server.system ? metricLevel(ramPct) : 'normal');
  const gpuLevel = $derived('normal');
  const diskLevel = $derived(storageSummary ? metricLevel(storagePct) : 'normal');
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

  $effect(() => {
    const timer = setInterval(() => {
      noteNowMs = Date.now();
    }, 1000);

    return () => clearInterval(timer);
  });
</script>

<article class="monitor-card bg-surface-card border border-surface-border" data-status={server.status}>
  <header class="monitor-card__header">
    <div class="monitor-card__title-row">
      <div class="monitor-card__title-stack">
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
          {#if showNetwork}
            <span class="monitor-card__network">{server.network === 'internal' ? '내부망' : '외부망'}</span>
          {/if}
        </div>

        <div class="monitor-card__meta">
          <span class="monitor-card__host">{hostText}</span>
          <span class="monitor-card__meta-separator" aria-hidden="true">·</span>
          <span class="monitor-card__refresh">{refreshText}</span>
        </div>

        {#if statusReasonText && server.status !== 'online'}
          <p class="monitor-card__reason" data-status={server.status} title={statusReasonText}>
            {statusReasonText}
          </p>
        {/if}
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

  {#if server.gpus.length > 0}
    <div class="monitor-card__gpu-list">
      {#each server.gpus as gpu (gpu.index)}
        <GpuBar
          {gpu}
          state={getCompactGpuState(server.status, server.last_seen, gpu)}
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
        >
          <span class="monitor-card__footer-toggle-main">
            <span class="monitor-card__footer-marker monitor-card__footer-marker--system" aria-hidden="true"></span>
            <span class="monitor-card__footer-label">시스템</span>
          </span>
          <span class="monitor-card__footer-side">
            {#if !sysExpanded}
              <span class="monitor-card__footer-preview monitor-card__system-preview">
                <span class="monitor-card__system-preview-item" data-level={cpuLevel} title={`CPU ${cpuPreviewText}`}>
                  <small>CPU</small>
                  <strong>{server.system ? `${cpuPct.toFixed(0)}%` : '–'}</strong>
                </span>
                <span class="monitor-card__system-preview-item" data-level={ramLevel} title={`RAM ${server.system ? `${ramPct.toFixed(0)}%` : '–'}`}>
                  <small>RAM</small>
                  <strong>{server.system ? `${ramPct.toFixed(0)}%` : '–'}</strong>
                </span>
                <span class="monitor-card__system-preview-item" data-level={gpuLevel} title={`GPU ${totalGpuPowerText}`}>
                  <small>GPU</small>
                  <strong>{totalGpuPowerText}</strong>
                </span>
                <span class="monitor-card__system-preview-item" data-level={diskLevel} title={`Disk ${diskPreviewText}`}>
                  <small>Disk</small>
                  <strong>{storageSummary ? `${storagePct.toFixed(0)}%` : '–'}</strong>
                </span>
              </span>
            {/if}
            <span class="monitor-card__footer-disclosure" class:is-expanded={sysExpanded} aria-hidden="true"></span>
          </span>
        </button>

        {#if sysExpanded}
          <div id={`system-panel-${server.server_id}`} class="monitor-card__footer-panel">
            <div class="monitor-card__metric-stack monitor-card__system-summary">
              <div class="monitor-card__summary-item" data-level={cpuLevel}>
                <div class="monitor-card__summary-head">
                  <span class="monitor-card__summary-label">CPU</span>
                  <span class="monitor-card__summary-value">{cpuPreviewText}</span>
                </div>
                <div class="monitor-meter" aria-hidden="true">
                  <div class="monitor-meter__fill monitor-meter__fill--util" style={`width: ${Math.min(100, cpuPct)}%`}></div>
                </div>
              </div>
              <div class="monitor-card__summary-item" data-level={ramLevel}>
                <div class="monitor-card__summary-head">
                  <span class="monitor-card__summary-label">RAM</span>
                  <span class="monitor-card__summary-value">{ramPreviewText}</span>
                </div>
                <div class="monitor-meter" aria-hidden="true">
                  <div class="monitor-meter__fill monitor-meter__fill--memory" style={`width: ${Math.min(100, ramPct)}%`}></div>
                </div>
              </div>
              <div class="monitor-card__summary-item" data-level={gpuLevel}>
                <div class="monitor-card__summary-head">
                  <span class="monitor-card__summary-label">GPU</span>
                  <span class="monitor-card__summary-value">{totalGpuPowerText}</span>
                </div>
                <div class="monitor-card__summary-meta">
                  {server.gpus.length > 0 ? `${server.gpus.length} devices` : 'GPU 없음'}
                </div>
              </div>
              <div class="monitor-card__summary-item" data-level={diskLevel}>
                <div class="monitor-card__summary-head">
                  <span class="monitor-card__summary-label">Disk</span>
                  <span class="monitor-card__summary-value">{diskPreviewText}</span>
                </div>
                <div class="monitor-meter" aria-hidden="true">
                  <div class="monitor-meter__fill monitor-meter__fill--storage" style={`width: ${Math.min(100, storagePct)}%`}></div>
                </div>
              </div>
            </div>

            {#if server.gpus.length > 0}
              <div class="monitor-card__subsection">
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
          </div>
        {/if}
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
          <span class="monitor-card__footer-marker monitor-card__footer-marker--memo" aria-hidden="true"></span>
          <span class="monitor-card__footer-label">메모</span>
        </span>
        <span class="monitor-card__footer-side">
          {#if !notesExpanded}
            <span class="monitor-card__footer-preview monitor-card__footer-preview--notes">
              {#if previewNotes.length > 0}
                <span class="monitor-card__note-preview-user">{previewNotes[0].username}</span>
                {#if previewNotes[0].kind === 'hold'}
                  <span class="monitor-card__note-preview-scope">
                    {#each holdGpuIndices(previewNotes[0]) as gpuIndex, index (gpuIndex)}{index > 0 ? '·' : ''}G{gpuIndex}{/each}
                  </span>
                {/if}
                <span class="monitor-card__note-preview-content">{previewNotes[0].content}</span>
                {#if previewNotes[0].expires_at}
                  <span class={`monitor-card__note-preview-expiry ${notePreviewBadgeClass(previewNotes[0])}`}>
                    {notePreviewCountdownText(previewNotes[0])}
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

      {#if notesExpanded}
        <div id={`notes-panel-${server.server_id}`} class="monitor-card__footer-panel">
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
                          <span class="monitor-note-item__user">{note.username}</span>
                          <span class="monitor-note-item__time">{noteDate(note.created_at)}</span>
                          {#if note.expires_at}
                            <span class="monitor-card__meta-separator" aria-hidden="true">·</span>
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
                onCreated={onNoteCreated}
              />
            </div>
          {/if}
        </div>
      {/if}
    </section>
  </div>
</article>
