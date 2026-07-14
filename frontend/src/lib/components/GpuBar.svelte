<script lang="ts">
  import type { GpuInfo } from '$lib/types';

  let { gpu }: { gpu: GpuInfo } = $props();

  const memUsedGB = $derived(Math.round(gpu.memory_used / 1024));
  const memTotalGB = $derived(Math.round(gpu.memory_total / 1024));
  const utilPct = $derived(Math.min(100, Math.max(0, gpu.utilization)));
  const utilValue = $derived(Math.round(utilPct));
  const memPct = $derived(
    gpu.memory_total > 0
      ? Math.min(100, (gpu.memory_used / gpu.memory_total) * 100)
      : 0
  );
  const isActive = $derived(gpu.users.length > 0);
  const gpuAriaLabel = $derived.by(() => {
    const usage = gpu.users.length > 0 ? gpu.users.join(', ') : 'idle';
    return `GPU ${gpu.index}, users ${usage}, utilization ${utilValue} percent, memory ${memUsedGB} of ${memTotalGB} gigabytes`;
  });
</script>

<div class="monitor-gpu-row" data-active={isActive ? 'true' : 'false'} aria-label={gpuAriaLabel}>
  <span class="monitor-gpu-row__index">G{gpu.index}</span>

  <div class="monitor-gpu-row__body">
    <div class="monitor-gpu-row__users">
      {#if gpu.users.length > 0}
        {#each gpu.users as user, index (`${gpu.index}-${user}-${index}`)}
          <span class="monitor-gpu-row__user">{user}</span>
        {/each}
      {:else}
        <span class="monitor-gpu-row__idle">idle</span>
      {/if}
    </div>

    <div class="monitor-gpu-row__metrics">
      <div class="monitor-gpu-metric">
        <span class="monitor-gpu-metric__label">Util</span>
        <div class="monitor-gpu-metric__track" aria-hidden="true">
          <div class="monitor-gpu-metric__fill monitor-gpu-metric__fill--util" style={`width: ${utilPct}%`}></div>
        </div>
        <span class="monitor-gpu-metric__value">{utilValue}%</span>
      </div>

      <div class="monitor-gpu-metric">
        <span class="monitor-gpu-metric__label">Mem</span>
        <div class="monitor-gpu-metric__track" aria-hidden="true">
          <div class="monitor-gpu-metric__fill monitor-gpu-metric__fill--memory" style={`width: ${memPct}%`}></div>
        </div>
        <span class="monitor-gpu-metric__value monitor-gpu-metric__value--memory">{memUsedGB}/{memTotalGB}GB</span>
      </div>
    </div>
  </div>
</div>
