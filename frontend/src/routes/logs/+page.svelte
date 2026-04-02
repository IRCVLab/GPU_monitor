<script lang="ts">
  import { browser } from '$app/environment';
  import { getLogs, getLogEventTypes, getServersWithOptions } from '$lib/api';
  import type { EventLog, EventSeverity, ServerRecord } from '$lib/types';

  // ── Filter state ──────────────────────────────────────────────
  let selectedServerId = $state<number | null>(null);
  let selectedEventType = $state<string | null>(null);
  let selectedSeverity = $state<EventSeverity | null>(null);

  // ── Pagination ────────────────────────────────────────────────
  const PAGE_SIZE = 50;
  let offset = $state(0);
  let total = $state(0);

  // ── Data ──────────────────────────────────────────────────────
  let logs = $state<EventLog[]>([]);
  let servers = $state<ServerRecord[]>([]);
  let eventTypes = $state<string[]>([]);
  let loading = $state(true);
  let loadError = $state('');
  let logsRequestVersion = 0;
  let logsAbortController: AbortController | null = null;
  let metadataAbortController: AbortController | null = null;

  // ── Row expand ────────────────────────────────────────────────
  let expandedId = $state<number | null>(null);

  // ── Dropdown open state ───────────────────────────────────────
  let openDropdown = $state<'server' | 'type' | 'severity' | null>(null);

  // ── Label maps ───────────────────────────────────────────────
  const severityLabel: Record<EventSeverity, string> = {
    critical: 'CRIT',
    warning: 'WARN',
    info: 'INFO'
  };

  function parseLogTime(iso: string): Date {
    return /(?:Z|[+-]\d{2}:\d{2})$/.test(iso) ? new Date(iso) : new Date(`${iso}Z`);
  }

  function abortLogsRequest() {
    logsAbortController?.abort();
    logsAbortController = null;
  }

  function abortMetadataRequest() {
    metadataAbortController?.abort();
    metadataAbortController = null;
  }

  function currentLogQuery(requestOffset: number) {
    return {
      server_id: selectedServerId ?? undefined,
      event_type: selectedEventType ?? undefined,
      severity: selectedSeverity ?? undefined,
      limit: PAGE_SIZE,
      offset: requestOffset
    };
  }

  // ── Load helpers ─────────────────────────────────────────────
  async function fetchLogs(reset = false): Promise<void> {
    const requestOffset = reset ? 0 : offset;
    const requestVersion = ++logsRequestVersion;
    abortLogsRequest();
    const controller = new AbortController();
    logsAbortController = controller;

    loading = true;
    loadError = '';
    if (reset) {
      offset = 0;
      logs = [];
      total = 0;
      expandedId = null;
    }
    try {
      const result = await getLogs(currentLogQuery(requestOffset), { signal: controller.signal });
      if (controller.signal.aborted || requestVersion !== logsRequestVersion) {
        return;
      }
      if (reset || requestOffset === 0) {
        logs = result.items;
      } else {
        logs = [...logs, ...result.items];
      }
      total = result.total;
      offset = requestOffset + result.items.length;
    } catch (e) {
      if (controller.signal.aborted || requestVersion !== logsRequestVersion) {
        return;
      }
      loadError = e instanceof Error ? e.message : '로그를 불러오지 못했습니다.';
    } finally {
      if (logsAbortController === controller) {
        logsAbortController = null;
      }
      if (requestVersion === logsRequestVersion) {
        loading = false;
      }
    }
  }

  async function initData(): Promise<void> {
    const initialLogsRequest = fetchLogs(true);
    abortMetadataRequest();
    const controller = new AbortController();
    metadataAbortController = controller;
    try {
      const [serverList, typeList] = await Promise.allSettled([
        getServersWithOptions({ signal: controller.signal }),
        getLogEventTypes({ signal: controller.signal })
      ]);
      if (controller.signal.aborted) {
        return;
      }
      if (serverList.status === 'fulfilled') servers = serverList.value;
      if (typeList.status === 'fulfilled') eventTypes = typeList.value;
    } catch {
      // non-critical
    } finally {
      if (metadataAbortController === controller) {
        metadataAbortController = null;
      }
    }
    await initialLogsRequest;
  }

  function initLogsRuntime() {
    if (!browser) return;

    const runtime = globalThis as typeof globalThis & {
      __monitoringV2LogsCleanup?: () => void;
    };
    runtime.__monitoringV2LogsCleanup?.();

    void initData();

    runtime.__monitoringV2LogsCleanup = () => {
      abortLogsRequest();
      abortMetadataRequest();
    };
  }

  initLogsRuntime();

  // ── Reactive filter reset ─────────────────────────────────────
  function applyFilter(): void {
    void fetchLogs(true);
  }

  function loadMore(): void {
    if (loading || logs.length >= total) return;
    void fetchLogs(false);
  }

  // ── Time formatting ───────────────────────────────────────────
  function absoluteTime(iso: string): string {
    return new Intl.DateTimeFormat('ko-KR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
      timeZoneName: 'short'
    }).format(parseLogTime(iso));
  }

  function rowClock(iso: string): string {
    return new Intl.DateTimeFormat('ko-KR', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }).format(parseLogTime(iso));
  }

  function rowDate(iso: string): string {
    return new Intl.DateTimeFormat('ko-KR', {
      month: '2-digit',
      day: '2-digit'
    }).format(parseLogTime(iso));
  }

  function eventTypeLabel(value: string): string {
    return value.replace(/_/g, ' ');
  }

  function serverLabel(log: EventLog): string {
    return log.server_name ?? 'SYSTEM';
  }

  // ── Server options ────────────────────────────────────────────
  const serverOptions = $derived([
    { value: null, label: '서버 전체' },
    ...servers.map((s) => ({ value: s.id, label: s.name }))
  ]);

  const typeOptions = $derived([
    { value: null, label: '카테고리 전체' },
    ...eventTypes.map((t) => ({ value: t, label: t }))
  ]);

  const severityOptions: { value: EventSeverity | null; label: string }[] = [
    { value: null, label: '레벨 전체' },
    { value: 'critical', label: 'Critical' },
    { value: 'warning', label: 'Warning' },
    { value: 'info', label: 'Info' }
  ];

  const hasMore = $derived(logs.length < total);
</script>

<div class="min-h-screen bg-surface text-white">

  <!-- ── Header ── -->
  <header class="border-b border-surface-border px-6 py-4">
    <div class="max-w-4xl mx-auto flex items-center justify-between">
      <div class="min-w-0">
        <div class="flex items-center gap-3">
          <a
            href="/"
            class="flex items-center gap-1.5 text-xs text-white/40 hover:text-white/70 transition-colors"
          >
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M15 18l-6-6 6-6"/>
            </svg>
            대시보드
          </a>
          <span class="text-white/15">/</span>
          <h1 class="text-sm font-semibold text-white/90 tracking-tight">이벤트 로그</h1>
        </div>
      </div>

      <div class="flex items-center gap-3">
        <span class="hidden sm:inline text-[11px] text-white/22 tabular-nums">
          {loading ? '갱신 중' : `총 ${total.toLocaleString()}건`}
        </span>
        <button
          class="btn-ghost flex items-center gap-1.5 text-xs"
          onclick={() => applyFilter()}
          disabled={loading}
        >
          <svg
            class="w-3.5 h-3.5 {loading ? 'animate-spin' : ''}"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          >
            <path d="M23 4v6h-6"/>
            <path d="M1 20v-6h6"/>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>
            <path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>
          </svg>
          새로고침
        </button>
      </div>
    </div>
  </header>

  <!-- ── Filter bar ── -->
  <div class="border-b border-surface-border px-6 py-3">
    <div class="max-w-4xl mx-auto flex items-center gap-2 flex-wrap">

      <!-- Server filter -->
      <div class="relative">
        <button
          class="filter-select {selectedServerId !== null ? 'active' : ''}"
          onclick={() => { openDropdown = openDropdown === 'server' ? null : 'server'; }}
          onblur={() => setTimeout(() => { if (openDropdown === 'server') openDropdown = null; }, 150)}
        >
          {selectedServerId !== null
            ? (serverOptions.find((o) => o.value === selectedServerId)?.label ?? '서버')
            : '서버 전체'}
          <svg
            class="w-3 h-3 text-white/30 transition-transform {openDropdown === 'server' ? 'rotate-180' : ''}"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
          >
            <path d="M6 9l6 6 6-6"/>
          </svg>
        </button>
        {#if openDropdown === 'server'}
          <div class="absolute top-full left-0 mt-1 z-20 min-w-[160px]
                      bg-surface-card border border-surface-border rounded-lg shadow-xl overflow-hidden">
            {#each serverOptions as opt}
              <button
                class="w-full text-left px-3 py-2 text-xs transition-colors
                       {selectedServerId === opt.value
                         ? 'text-white bg-white/5'
                         : 'text-white/50 hover:bg-white/5 hover:text-white/80'}"
                onclick={() => {
                  selectedServerId = opt.value as number | null;
                  openDropdown = null;
                  applyFilter();
                }}
              >
                {opt.label}
              </button>
            {/each}
          </div>
        {/if}
      </div>

      <!-- Event type filter -->
      <div class="relative">
        <button
          class="filter-select {selectedEventType !== null ? 'active' : ''}"
          onclick={() => { openDropdown = openDropdown === 'type' ? null : 'type'; }}
          onblur={() => setTimeout(() => { if (openDropdown === 'type') openDropdown = null; }, 150)}
        >
          {selectedEventType ?? '카테고리 전체'}
          <svg
            class="w-3 h-3 text-white/30 transition-transform {openDropdown === 'type' ? 'rotate-180' : ''}"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
          >
            <path d="M6 9l6 6 6-6"/>
          </svg>
        </button>
        {#if openDropdown === 'type'}
          <div class="absolute top-full left-0 mt-1 z-20 min-w-[160px]
                      bg-surface-card border border-surface-border rounded-lg shadow-xl overflow-hidden">
            {#each typeOptions as opt}
              <button
                class="w-full text-left px-3 py-2 text-xs transition-colors
                       {selectedEventType === opt.value
                         ? 'text-white bg-white/5'
                         : 'text-white/50 hover:bg-white/5 hover:text-white/80'}"
                onclick={() => {
                  selectedEventType = opt.value as string | null;
                  openDropdown = null;
                  applyFilter();
                }}
              >
                {opt.label}
              </button>
            {/each}
          </div>
        {/if}
      </div>

      <!-- Severity filter -->
      <div class="relative">
        <button
          class="filter-select {selectedSeverity !== null ? 'active' : ''}"
          onclick={() => { openDropdown = openDropdown === 'severity' ? null : 'severity'; }}
          onblur={() => setTimeout(() => { if (openDropdown === 'severity') openDropdown = null; }, 150)}
        >
          {selectedSeverity
            ? severityOptions.find((o) => o.value === selectedSeverity)?.label
            : '레벨 전체'}
          <svg
            class="w-3 h-3 text-white/30 transition-transform {openDropdown === 'severity' ? 'rotate-180' : ''}"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
          >
            <path d="M6 9l6 6 6-6"/>
          </svg>
        </button>
        {#if openDropdown === 'severity'}
          <div class="absolute top-full left-0 mt-1 z-20 min-w-[140px]
                      bg-surface-card border border-surface-border rounded-lg shadow-xl overflow-hidden">
            {#each severityOptions as opt}
              <button
                class="w-full text-left px-3 py-2 text-xs transition-colors
                       {selectedSeverity === opt.value
                         ? 'text-white bg-white/5'
                         : 'text-white/50 hover:bg-white/5 hover:text-white/80'}"
                onclick={() => {
                  selectedSeverity = opt.value as EventSeverity | null;
                  openDropdown = null;
                  applyFilter();
                }}
              >
                {opt.label}
              </button>
            {/each}
          </div>
        {/if}
      </div>

      <!-- Total count -->
      <span class="ml-auto text-xs text-white/25 tabular-nums">
        {#if loading && logs.length === 0}
          로딩 중...
        {:else}
          총 {total.toLocaleString()}건
        {/if}
      </span>
    </div>
  </div>

  <!-- ── Log list ── -->
  <main class="max-w-4xl mx-auto">

    <!-- Close dropdowns when clicking outside -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    {#if openDropdown !== null}
      <div
        class="fixed inset-0 z-10"
        onclick={() => { openDropdown = null; }}
      ></div>
    {/if}

    {#if loadError}
      <div class="flex flex-col items-center justify-center gap-3 py-16 text-white/40 text-sm">
        <span>{loadError}</span>
        <button class="btn-ghost text-xs border border-white/10 rounded-lg px-3 py-1.5"
          onclick={() => applyFilter()}>
          다시 시도
        </button>
      </div>
    {:else if loading && logs.length === 0}
      <div class="flex items-center justify-center gap-2 py-16 text-white/30 text-sm">
        <div class="w-4 h-4 border border-white/20 border-t-white/50 rounded-full animate-spin"></div>
        불러오는 중...
      </div>
    {:else if logs.length === 0}
      <div class="flex items-center justify-center py-16 text-white/25 text-sm">
        로그가 없습니다.
      </div>
    {:else}
      <div>
        {#each logs as log (log.id)}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div
            class="log-row severity-{log.severity} {expandedId === log.id ? 'expanded' : ''}"
            onclick={() => { expandedId = expandedId === log.id ? null : log.id; }}
          >
            <div class="log-severity-stack">
              <span class="log-severity-dot"></span>
              <span class="log-severity-pill">{severityLabel[log.severity]}</span>
            </div>

            <div class="log-main">
              <div class="flex items-start gap-3">
                <div class="min-w-0 flex-1">
                  <p class="log-message">{log.message}</p>
                  <div class="log-meta-strip">
                    <span class="log-chip log-chip-server">{serverLabel(log)}</span>
                    {#if log.server_id !== null}
                      <span class="log-chip log-chip-id">#{log.server_id}</span>
                    {/if}
                    <span class="log-chip log-chip-event">{eventTypeLabel(log.event_type)}</span>
                    <span class="log-chip log-chip-time sm:hidden">{rowClock(log.created_at)}</span>
                  </div>
                </div>

                <div class="log-time-block hidden sm:flex">
                  <span class="log-time-clock">{rowClock(log.created_at)}</span>
                  <span class="log-time-date">{rowDate(log.created_at)}</span>
                </div>

                <span class="log-disclosure" aria-hidden="true">
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25">
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </span>
              </div>

              {#if expandedId === log.id}
                <div class="log-detail-panel">
                  <div class="log-detail-grid">
                    <div class="log-detail-item">
                      <span class="log-detail-label">발생 시각</span>
                      <span class="log-detail-value">{absoluteTime(log.created_at)}</span>
                    </div>
                    <div class="log-detail-item">
                      <span class="log-detail-label">서버</span>
                      <span class="log-detail-value">
                        {serverLabel(log)}{log.server_id !== null ? ` · #${log.server_id}` : ''}
                      </span>
                    </div>
                    <div class="log-detail-item">
                      <span class="log-detail-label">이벤트</span>
                      <span class="log-detail-value">{log.event_type}</span>
                    </div>
                  </div>
                  {#if log.metadata && Object.keys(log.metadata).length > 0}
                    <pre class="log-detail-json">{JSON.stringify(log.metadata, null, 2)}</pre>
                  {:else}
                    <p class="log-detail-empty">추가 데이터 없음</p>
                  {/if}
                </div>
              {/if}
            </div>
          </div>
        {/each}

        <!-- Load more -->
        {#if hasMore}
          <div class="px-4 py-4 flex justify-center">
            <button
              class="text-xs text-white/30 hover:text-white/60 transition-colors flex items-center gap-2"
              onclick={loadMore}
              disabled={loading}
            >
              {#if loading}
                <span class="w-3 h-3 border border-white/20 border-t-white/50 rounded-full animate-spin"></span>
              {:else}
                더 보기 ({total - logs.length}건 남음)
              {/if}
            </button>
          </div>
        {/if}
      </div>
    {/if}
  </main>
</div>
