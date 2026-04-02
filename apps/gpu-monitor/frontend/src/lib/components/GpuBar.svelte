<script lang="ts">
  import type { GpuInfo } from '$lib/types';

  let { gpu }: { gpu: GpuInfo } = $props();

  const memUsedGB  = $derived((gpu.memory_used  / 1024).toFixed(1));
  const memTotalGB = $derived((gpu.memory_total / 1024).toFixed(1));
  const utilPct    = $derived(Math.min(100, Math.max(0, gpu.utilization)));
  const memPct     = $derived(
    gpu.memory_total > 0
      ? Math.min(100, (gpu.memory_used / gpu.memory_total) * 100)
      : 0
  );
  const usersText = $derived(gpu.users.length > 0 ? gpu.users.join(', ') : 'idle');
  const isActive = $derived(gpu.users.length > 0);
</script>

<div class="gpu-row grid min-w-0 grid-cols-[auto,minmax(0,1fr)] gap-x-2 gap-y-1.5">
  <span
    class="gpu-row-index row-span-2 inline-flex items-center justify-center self-start rounded-full border px-2 py-1 text-[11px] font-mono shrink-0 {isActive
      ? 'border-emerald-300/20 bg-emerald-400/12 text-emerald-200'
      : 'border-white/8 bg-white/[0.04] text-white/35'}"
  >
    G{gpu.index}
  </span>

  <div class="min-w-0 self-center">
    {#if gpu.users.length > 0}
      <span
        class="gpu-row-users block truncate text-[11px] leading-[1.3] text-sky-300/90"
        title={usersText}
      >
        {usersText}
      </span>
    {:else}
      <span class="gpu-row-users block text-[11px] leading-[1.2] text-white/30">idle</span>
    {/if}
  </div>

  <div class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
    <div class="gpu-row-util flex min-w-[6.25rem] flex-1 items-center gap-1.5">
      <span class="gpu-row-label text-[10px] uppercase tracking-[0.16em] text-white/22 shrink-0">U</span>
      <div class="bar-track flex-1 h-1.5">
        <div
          class="h-full rounded-full bg-emerald-500/90 transition-all duration-300"
          style="width: {utilPct}%"
        ></div>
      </div>
      <span class="gpu-row-value font-mono text-[11px] text-white/68 w-8 text-right shrink-0">{gpu.utilization}%</span>
    </div>

    <div class="gpu-row-mem flex min-w-[7rem] flex-1 items-center gap-1.5">
      <span class="gpu-row-label text-[10px] uppercase tracking-[0.16em] text-white/22 shrink-0">M</span>
      <div class="bar-track flex-1 h-1.5">
        <div
          class="h-full rounded-full bg-blue-500/90 transition-all duration-300"
          style="width: {memPct}%"
        ></div>
      </div>
      <span class="gpu-row-value font-mono text-[11px] text-white/68 whitespace-nowrap shrink-0">{memUsedGB}/{memTotalGB}G</span>
    </div>
  </div>
</div>
