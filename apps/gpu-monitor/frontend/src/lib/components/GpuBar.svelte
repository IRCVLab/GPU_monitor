<script lang="ts">
  import { cubicOut } from 'svelte/easing';
  import { prefersReducedMotion } from 'svelte/motion';
  import { fly } from 'svelte/transition';
  import type { GpuInfo, Note } from '$lib/types';
  import type { CompactGpuState } from '$lib/utils/compactGpuAvailability';
  import { buildHoldAdvisory, getNotePriorityMeta, resolveDisplayName } from '$lib/utils/noteAdvisory';

  type AdvisoryHoldCue = {
    note: Note;
    remaining: string;
  };

  let {
    gpu,
    state: availabilityState,
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
  const displayUsers = $derived.by(() => [...gpu.users].sort());
  const displayUsersSignature = $derived(displayUsers.join('\u0000') || 'idle');
  const identityInFly = $derived({
    y: prefersReducedMotion.current ? 0 : 2,
    opacity: prefersReducedMotion.current ? 1 : 0,
    duration: prefersReducedMotion.current ? 0 : 220,
    easing: cubicOut
  });
  const identityOutFly = $derived({
    y: prefersReducedMotion.current ? 0 : -2,
    opacity: prefersReducedMotion.current ? 1 : 0,
    duration: prefersReducedMotion.current ? 0 : 160,
    easing: cubicOut
  });
  let tooltipOpen = $state(false);

  const holdAdvisory = $derived(buildHoldAdvisory(advisoryHolds.map(({ note }) => note)));
  const primaryHold = $derived(holdAdvisory.primary);
  const primaryPriorityMeta = $derived(primaryHold ? getNotePriorityMeta(primaryHold.priority) : null);
  const primaryHoldDisplayName = $derived(primaryHold ? resolveDisplayName(primaryHold) : '');
  const orderedHoldEntries = $derived.by(() =>
    holdAdvisory.ordered
      .map((note) => advisoryHolds.find((entry) => entry.note.id === note.id))
      .filter((entry): entry is AdvisoryHoldCue => Boolean(entry))
  );
  const tooltipId = $derived(`gpu-hold-tooltip-${gpu.index}`);
  const holdDetailText = $derived.by(() =>
    orderedHoldEntries
      .map((entry, index) => {
        const priorityMeta = getNotePriorityMeta(entry.note.priority);
        const detail = [`HOLD ${index + 1}`, resolveDisplayName(entry.note), priorityMeta.label];
        if (entry.remaining) detail.push(entry.remaining);
        if (entry.note.content) detail.push(entry.note.content);
        return detail.join(' · ');
      })
      .join('; ')
  );
  const holdAriaDetail = $derived(holdDetailText ? `, advisory ${holdDetailText}` : '');
  const gpuAriaLabel = $derived.by(() => {
    const usage = displayUsers.length > 0 ? displayUsers.join(', ') : 'idle';
    return `GPU ${gpu.index}, users ${usage}, utilization ${utilValue} percent, memory ${memUsedGB} of ${memTotalGB} gigabytes${holdAriaDetail}`;
  });

  function openTooltip() {
    tooltipOpen = true;
  }

  function closeTooltip() {
    tooltipOpen = false;
  }

  function handleTooltipKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      closeTooltip();
    }
  }
</script>

<div class="monitor-gpu-row" data-state={availabilityState} aria-label={gpuAriaLabel}>
  <span class="monitor-gpu-row__index" data-has-hold={primaryHold ? 'true' : 'false'}>G{gpu.index}</span>

  <div class="monitor-gpu-row__body">
    <div class="monitor-gpu-row__users">
      <div class="monitor-gpu-row__identity-slot">
        {#key displayUsersSignature}
          <div class="monitor-gpu-row__identity-set" in:fly={identityInFly} out:fly={identityOutFly}>
            {#if displayUsers.length > 0}
              {#each displayUsers as user, index (`${gpu.index}-${user}-${index}`)}
                <span class="monitor-gpu-row__user">{user}</span>
              {/each}
            {:else}
              <span class="monitor-gpu-row__idle">idle</span>
            {/if}
          </div>
        {/key}
      </div>

      {#if primaryHold}
        <button
          type="button"
          class={`monitor-gpu-row__hold-cue ${primaryPriorityMeta?.className ?? ''}`}
          aria-describedby={tooltipOpen ? tooltipId : undefined}
          aria-label={holdDetailText}
          onmouseenter={openTooltip}
          onmouseleave={closeTooltip}
          onfocus={openTooltip}
          onblur={closeTooltip}
          onkeydown={handleTooltipKeydown}
        >
          {#if primaryHold.priority !== 'normal'}
            <span class="monitor-gpu-row__hold-priority">{primaryPriorityMeta?.label}</span>
          {/if}
          <span class="monitor-gpu-row__hold-owner">{primaryHoldDisplayName}</span>
          {#if holdAdvisory.secondarySummary}
            <span class="monitor-gpu-row__hold-more">{holdAdvisory.secondarySummary}</span>
          {/if}
        </button>
        {#if tooltipOpen}
          <div id={tooltipId} role="tooltip" class="monitor-gpu-row__tooltip">
            <div class="monitor-gpu-row__tooltip-title">GPU G{gpu.index} · {gpu.name}</div>
            {#each orderedHoldEntries as entry (entry.note.id)}
              {@const priorityMeta = getNotePriorityMeta(entry.note.priority)}
              <div class="monitor-gpu-row__tooltip-note">
                <div class="monitor-gpu-row__tooltip-note-head">
                  <span class="monitor-gpu-row__tooltip-note-owner">{resolveDisplayName(entry.note)}</span>
                  <span class={`monitor-gpu-row__tooltip-note-priority ${priorityMeta.className}`}>{priorityMeta.label}</span>
                </div>
                <div class="monitor-gpu-row__tooltip-note-meta">
                  <span class="monitor-gpu-row__tooltip-note-expiry">{entry.remaining}</span>
                </div>
                <p class="monitor-gpu-row__tooltip-note-memo">{entry.note.content}</p>
              </div>
            {/each}
          </div>
        {/if}
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
