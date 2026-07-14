<script lang="ts">
  import type { GpuInfo } from '$lib/types';

  let { gpu }: { gpu: GpuInfo } = $props();

  const memUsedGB = $derived(Math.round(gpu.memory_used / 1024));
  const memTotalGB = $derived(Math.round(gpu.memory_total / 1024));
  const utilPct = $derived(Math.min(100, Math.max(0, gpu.utilization)));
  const utilValue = $derived(Math.round(utilPct));
  const isActive = $derived(gpu.users.length > 0);
  const gpuAriaLabel = $derived.by(() => {
    const usage = gpu.users.length > 0 ? gpu.users.join(', ') : 'idle';
    return `GPU ${gpu.index}, users ${usage}, utilization ${utilValue} percent, memory ${memUsedGB} of ${memTotalGB} gigabytes`;
  });
</script>

<div class="monitor-gpu-row" data-active={isActive ? 'true' : 'false'} aria-label={gpuAriaLabel}>
  <div class="monitor-gpu-row__body">
    <div class="monitor-gpu-row__primary">
      <span class="monitor-gpu-row__index">G{gpu.index}</span>

      <div class="monitor-gpu-row__users">
        {#if gpu.users.length > 0}
          {#each gpu.users as user, index (`${gpu.index}-${user}-${index}`)}
            <span class="monitor-gpu-row__user">{user}</span>
          {/each}
        {:else}
          <span class="monitor-gpu-row__idle">idle</span>
        {/if}
      </div>
    </div>

    <div class="monitor-gpu-row__secondary">
      <span class="monitor-gpu-row__metric">
        <span class="monitor-gpu-row__metric-label">Util</span>
        <span class="monitor-gpu-row__metric-value">{utilValue}%</span>
      </span>
      <span class="monitor-gpu-row__metric-separator" aria-hidden="true">·</span>
      <span class="monitor-gpu-row__metric">
        <span class="monitor-gpu-row__metric-label">Mem</span>
        <span class="monitor-gpu-row__metric-value">{memUsedGB}/{memTotalGB} GB</span>
      </span>
    </div>
  </div>
</div>
