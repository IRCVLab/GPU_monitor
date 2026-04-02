<script lang="ts">
  import type { ServerState, Note } from '$lib/types';
  import { getNotes, deleteNote } from '$lib/api';
  import StatusBadge from '$lib/components/StatusBadge.svelte';
  import GpuBar from '$lib/components/GpuBar.svelte';
  import NoteForm from '$lib/components/NoteForm.svelte';

  let {
    server,
    onEdit
  }: {
    server: ServerState;
    onEdit?: (server: ServerState) => void;
  } = $props();

  // --- Local UI state ---
  let sysExpanded   = $state(false);
  let notesExpanded = $state(false);
  let notes         = $state<Note[]>([]);
  let notesLoaded   = $state(false);
  let notesLoading  = $state(false);
  let notesError    = $state('');
  let notesServerId = $state<number | null>(null);

  // Delete state per note id
  let deleteState    = $state<Record<number, { loading: boolean; error: string }>>({});
  let deletePassword = $state<Record<number, string>>({});

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

  const lastSeenAbsoluteText = $derived(absoluteTime(server.last_seen));
  const hostText = $derived(server.port ? `${server.host}:${server.port}` : server.host);
  const statusReasonText = $derived(server.status_reason?.message ?? '');
  const statusReasonClass = $derived(
    server.status === 'offline'
      ? 'text-red-400/70'
      : server.status === 'degraded'
        ? 'text-amber-300/78'
        : 'text-white/34'
  );

  // --- System info helpers ---
  const cpuPct   = $derived(server.system?.cpu_percent ?? 0);
  const ramUsed  = $derived(server.system ? (server.system.ram_used  / 1024).toFixed(1) : '–');
  const ramTotal = $derived(server.system ? (server.system.ram_total / 1024).toFixed(1) : '–');
  const ramPct   = $derived(
    server.system && server.system.ram_total > 0
      ? (server.system.ram_used / server.system.ram_total) * 100
      : 0
  );
  const totalGpuPower = $derived(server.gpus.reduce((sum, gpu) => sum + Math.max(0, gpu.power_draw || 0), 0));
  const totalGpuPowerText = $derived(server.gpus.length > 0 ? `${Math.round(totalGpuPower)}W` : '–');
  const systemPreviewText = $derived(
    `CPU ${server.system ? `${cpuPct.toFixed(0)}%` : '–'} · RAM ${server.system ? `${ramUsed}/${ramTotal}GB` : '–'} · GPU ${totalGpuPowerText}`
  );
  const storageSummary = $derived(server.storage?.summary ?? null);
  const storageUsedText = $derived(storageSummary ? formatStorage(storageSummary.used) : '–');
  const storageTotalText = $derived(storageSummary ? formatStorage(storageSummary.total) : '–');
  const storagePct = $derived(storageSummary?.percent ?? 0);
  const storageMounts = $derived([...(server.storage?.mounts ?? [])].sort((a, b) => b.percent - a.percent || a.mount.localeCompare(b.mount)));
  const previewNotes = $derived(notes.slice(0, 2));

  // --- Notes ---
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
    } catch (e) {
      notesError = e instanceof Error ? e.message : '메모를 불러오지 못했습니다.';
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
    const pw = deletePassword[note.id] ?? '';
    if (!pw.trim()) return;
    deleteState = { ...deleteState, [note.id]: { loading: true, error: '' } };
    try {
      await deleteNote(server.server_id, note.id, note.username, pw);
      notes = notes.filter((n) => n.id !== note.id);
      const { [note.id]: _removed, ...rest } = deleteState;
      deleteState = rest;
    } catch (e) {
      deleteState = {
        ...deleteState,
        [note.id]: { loading: false, error: e instanceof Error ? e.message : '삭제 실패' }
      };
    }
  }

  function noteDate(iso: string): string {
    const d = new Date(iso);
    return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
  }

  const cardBorder = $derived(server.status === 'offline' ? 'border-red-900/50' : 'border-surface-border');

  $effect(() => {
    const serverId = server.server_id;
    if (notesServerId === serverId) return;

    notesServerId = serverId;
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

<div class="server-card bg-surface-card border {cardBorder} rounded-xl overflow-hidden">

  <!-- ── Header ── -->
  <div class="server-card-header px-4 pt-3 pb-2.5">
    <div class="flex items-center gap-2">
      <span class="server-card-title font-semibold text-sm text-white/90 truncate flex-1 min-w-0">
        {server.server_name}
      </span>
      <span class="server-card-network-chip text-xs bg-white/10 rounded px-1.5 py-0.5 text-white/50 shrink-0">
        {server.network === 'internal' ? '내부망' : '외부망'}
      </span>
      <StatusBadge status={server.status} />
      {#if onEdit}
        <button
          onclick={() => onEdit(server)}
          class="w-6 h-6 flex items-center justify-center rounded text-white/30 hover:text-white/70 hover:bg-white/10 transition-colors shrink-0"
          aria-label="서버 편집"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
      {/if}
    </div>

    <div class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <span class="server-card-host text-xs text-white/30 font-mono">{hostText}</span>
      {#if server.status === 'offline'}
        <span class="server-card-seen text-xs text-red-300/72 font-mono">{lastSeenAbsoluteText}</span>
      {:else}
        <span class="server-card-seen text-xs text-white/46 font-mono">{lastSeenAbsoluteText}</span>
      {/if}
      {#if statusReasonText && server.status !== 'online'}
        <span class="text-xs text-white/12">·</span>
        <span
          class="server-card-status-reason max-w-full truncate text-[11px] {statusReasonClass}"
          title={statusReasonText}
        >
          {statusReasonText}
        </span>
      {/if}
    </div>
  </div>

  <!-- ── GPU rows ── -->
  {#if server.gpus.length > 0}
    <div class="server-card-gpu-section border-t border-surface-border px-4 py-2 flex flex-col gap-1">
      {#each server.gpus as gpu}
        <GpuBar {gpu} />
      {/each}
    </div>
  {/if}

  <!-- ── System info (collapsible) ── -->
  {#if server.system || server.storage || server.gpus.length > 0}
    <div class="border-t border-surface-border">
      <button
        onclick={() => (sysExpanded = !sysExpanded)}
        class="server-card-toggle w-full flex items-center gap-1.5 px-4 py-2 text-xs text-white/40 hover:bg-white/5 transition-colors text-left"
      >
        <span class="flex shrink-0 items-center gap-1.5">
          <span>{sysExpanded ? '▲' : '▼'}</span>
          <span>시스템</span>
        </span>
        {#if !sysExpanded}
          <span
            class="server-card-system-preview ml-1 min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-left font-mono text-white/30"
            title={systemPreviewText}
          >
            {systemPreviewText}
          </span>
        {/if}
      </button>

      {#if sysExpanded}
        <div class="server-card-panel server-card-system-panel px-4 pb-3 flex flex-col gap-1.5">
          {#if server.system}
            <div class="flex items-center gap-2.5">
              <span class="w-9 shrink-0 text-[11px] text-white/38">CPU</span>
              <div class="bar-track !h-1 flex-1">
                <div
                  class="h-full rounded-full bg-emerald-500/70 transition-all duration-300"
                  style="width: {cpuPct}%"
                ></div>
              </div>
              <span class="w-7 shrink-0 text-right font-mono text-[11px] text-white/58">{cpuPct.toFixed(0)}%</span>
            </div>
            <div class="flex items-center gap-2.5">
              <span class="w-9 shrink-0 text-[11px] text-white/38">RAM</span>
              <div class="bar-track !h-1 flex-1">
                <div
                  class="h-full rounded-full bg-blue-500/70 transition-all duration-300"
                  style="width: {ramPct}%"
                ></div>
              </div>
              <span class="shrink-0 whitespace-nowrap font-mono text-[11px] text-white/58">{ramUsed}/{ramTotal}GB</span>
            </div>
          {/if}

          <!-- GPU hardware info -->
          {#if server.gpus.length > 0}
            <div class="mt-1 pt-1 border-t border-white/5">
              <div class="grid grid-cols-2 gap-x-2 gap-y-1 text-[10px] leading-none tabular-nums md:grid-cols-4">
              {#each server.gpus as gpu}
                <span class="inline-flex min-w-0 items-center gap-1 rounded-md border border-white/[0.04] bg-white/[0.02] px-1.5 py-1">
                  <span class="font-mono text-white/26">G{gpu.index}</span>
                  <span class="{gpu.temperature > 85 ? 'text-red-400/82' : gpu.temperature > 70 ? 'text-amber-300/78' : 'text-white/40'}">
                    {gpu.temperature}°C
                  </span>
                  <span class="text-white/24">
                    {Math.round(gpu.power_draw)}W
                  </span>
                </span>
              {/each}
              </div>
            </div>
          {/if}

          {#if server.storage}
            <div class="mt-1 pt-1 border-t border-white/5">
              <div class="flex items-center gap-2">
                <span class="text-[11px] uppercase tracking-[0.16em] text-white/26">Storage</span>
                <span class="font-mono text-[11px] text-white/58">{storageUsedText}/{storageTotalText}</span>
                <span class="text-[10px] text-white/24">{server.storage.summary.mount_count} mounts</span>
                {#if server.storage.collected_at}
                  <span class="ml-auto text-[10px] font-mono text-white/24">{absoluteTime(server.storage.collected_at)}</span>
                {/if}
              </div>
              <div class="mt-1.5 flex items-center gap-2.5">
                <span class="w-9 shrink-0 text-[11px] text-white/38">Disk</span>
                <div class="bar-track !h-1 flex-1">
                  <div
                    class="h-full rounded-full bg-slate-400/70 transition-all duration-300"
                    style="width: {Math.min(100, storagePct)}%"
                  ></div>
                </div>
                <span class="w-8 shrink-0 text-right font-mono text-[11px] text-white/58">{storagePct.toFixed(0)}%</span>
              </div>
              {#if storageMounts.length > 0}
                <div class="mt-1.5 flex max-h-36 flex-col gap-1 overflow-y-auto pr-1">
                  {#each storageMounts as mount}
                    <div class="flex items-center gap-2 rounded-md border border-white/[0.04] bg-white/[0.02] px-2 py-1 text-[11px] leading-none">
                      <span class="min-w-0 flex-1 truncate font-mono text-white/60" title={mount.mount}>{mount.mount}</span>
                      <span class="shrink-0 font-mono text-white/40">{formatStorage(mount.used)}/{formatStorage(mount.size)}</span>
                      <span class="w-9 shrink-0 text-right font-mono {mount.percent >= 90 ? 'text-red-400/82' : mount.percent >= 75 ? 'text-amber-300/78' : 'text-white/30'}">{mount.percent.toFixed(0)}%</span>
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {/if}

  <!-- ── Notes (collapsible) ── -->
  <div class="border-t border-surface-border">
    <button
      onclick={toggleNotes}
      class="server-card-toggle w-full flex items-center gap-2 px-4 py-2 text-xs text-white/40 hover:bg-white/5 transition-colors text-left"
    >
      <span>{notesExpanded ? '▲' : '▼'}</span>
      <span>메모</span>
      {#if notes.length > 0}
        <span class="server-card-note-count bg-white/10 text-white/50 rounded px-1.5 py-0.5 font-mono">{notes.length}</span>
      {/if}
      {#if !notesExpanded}
        <span class="server-card-note-preview ml-auto flex min-w-0 items-center justify-end gap-1 text-[11px]">
          {#if previewNotes.length > 0}
            <span class="server-card-note-preview-user shrink-0 text-sky-300/78">{previewNotes[0].username}</span>
            <span class="server-card-note-preview-separator shrink-0 text-white/12">·</span>
            <span class="server-card-note-preview-content min-w-0 max-w-[13rem] truncate sm:max-w-[16rem]">{previewNotes[0].content}</span>
          {:else if notesLoaded}
            <span class="server-card-note-preview-empty">+ 메모</span>
          {/if}
        </span>
      {/if}
    </button>

    {#if notesExpanded}
      <div class="server-card-panel server-card-notes-panel px-4 pb-3">
        {#if notesLoading}
          <div class="flex items-center gap-2 py-2 text-xs text-white/30">
            <span class="inline-block w-3 h-3 border border-white/20 border-t-white/50 rounded-full animate-spin"></span>
            불러오는 중...
          </div>
        {:else if notesError}
          <div class="flex items-center justify-between py-2">
            <span class="text-xs text-red-400/70">{notesError}</span>
            <button
              onclick={() => void loadNotes(true)}
              class="text-xs text-white/40 hover:text-white/70 transition-colors ml-3 shrink-0"
            >
              다시 시도
            </button>
          </div>
        {:else}
          {#each notes as note (note.id)}
            <div class="server-card-note-item py-2 border-b border-white/5 last:border-0">
              <div class="min-w-0">
                <div class="server-card-note-meta mb-0.5 flex items-center gap-2">
                  <span class="server-card-note-user text-xs text-sky-300/78 font-medium">{note.username}</span>
                  <span class="server-card-note-time text-xs text-white/25 font-mono">{noteDate(note.created_at)}</span>
                </div>
                <p class="server-card-note-content text-xs text-white/70 whitespace-pre-wrap break-words">{note.content}</p>
                {#if deleteState[note.id]?.error}
                  <p class="mt-1 text-xs text-red-400">{deleteState[note.id].error}</p>
                {/if}
                <div class="server-card-note-actions mt-1.5 flex items-center justify-end gap-1">
                  <input
                    type="password"
                    placeholder="비밀번호"
                    bind:value={deletePassword[note.id]}
                    class="server-card-note-password w-20 rounded border border-surface-border bg-white/5 px-1.5 py-0.5 text-xs text-white/60 placeholder:text-white/20 focus:outline-none focus:border-white/20"
                  />
                  <button
                    onclick={() => handleDelete(note)}
                    disabled={deleteState[note.id]?.loading}
                    class="server-card-note-delete text-xs text-red-400/60 hover:text-red-400 transition-colors active:scale-95 disabled:opacity-40"
                  >
                    {#if deleteState[note.id]?.loading}
                      <span class="inline-block w-3 h-3 border border-white/20 border-t-white/50 rounded-full animate-spin"></span>
                    {:else}
                      삭제
                    {/if}
                  </button>
                </div>
              </div>
            </div>
          {/each}

          <NoteForm serverId={server.server_id} onCreated={onNoteCreated} />
        {/if}
      </div>
    {/if}
  </div>
</div>
