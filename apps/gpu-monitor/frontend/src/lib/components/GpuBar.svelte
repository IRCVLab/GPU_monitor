<script lang="ts">
  import type { GpuInfo } from '$lib/types';
  import type { CompactGpuState } from '$lib/utils/compactGpuAvailability';

  type AdvisoryHoldCue = {
    owner: string;
    remaining: string;
    memo: string;
  };

  let {
    gpu,
    state,
    advisoryHolds = []
  }: {
    gpu: GpuInfo;
    state: CompactGpuState;
    advisoryHolds?: AdvisoryHoldCue[];
  } = $props();

  const memUsedGB = $derived(Math.round(gpu.memory_used / 1024));
  const memTotalGB = $derived(Math.round(gpu.memory_total / 1024));
  const utilPct = $derived(Math.min(100, Math.max(0, gpu.utilization)));
  const utilValue = $derived(Math.round(utilPct));
  const memPct = $derived(
    gpu.memory_total > 0
      ? Math.min(100, (gpu.memory_used / gpu.memory_total) * 100)
      : 0
  );
  const primaryHold = $derived(advisoryHolds[0] ?? null);
  const holdDetailText = $derived.by(() =>
    advisoryHolds
      .map((hold, index) => {
        const detail = [`HOLD ${index + 1}`, hold.owner];
        if (hold.remaining) detail.push(hold.remaining);
        if (hold.memo) detail.push(hold.memo);
        return detail.join(' · ');
      })
      .join('; ')
  );
  const holdAriaDetail = $derived(holdDetailText ? `, advisory ${holdDetailText}` : '');
  const gpuAriaLabel = $derived.by(() => {
    const usage = gpu.users.length > 0 ? gpu.users.join(', ') : 'idle';
    return `GPU ${gpu.index}, users ${usage}, utilization ${utilValue} percent, memory ${memUsedGB} of ${memTotalGB} gigabytes${holdAriaDetail}`;
  });
</script>

<div class="monitor-gpu-row" data-state={state} aria-label={gpuAriaLabel}>
  <span class="monitor-gpu-row__index" data-has-hold={primaryHold ? 'true' : 'false'}>G{gpu.index}</span>

  <div class="monitor-gpu-row__body">
    <div class="monitor-gpu-row__users">
      {#if gpu.users.length > 0}
        {#each gpu.users as user, index (`${gpu.index}-${user}-${index}`)}
          <span class="monitor-gpu-row__user">{user}</span>
        {/each}
      {:else}
        <span class="monitor-gpu-row__idle">idle</span>
      {/if}

      {#if primaryHold}
        <span class="monitor-gpu-row__hold-cue" title={holdDetailText} aria-label={holdDetailText}>
          HOLD {primaryHold.owner}{#if advisoryHolds.length > 1} +{advisoryHolds.length - 1}{/if}
        </span>
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
