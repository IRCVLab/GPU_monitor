<script lang="ts">
  import { onDestroy } from 'svelte';
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
  const holdAdvisory = $derived(buildHoldAdvisory(advisoryHolds.map(({ note }) => note)));
  const primaryHold = $derived(holdAdvisory.primary);
  const secondaryHolds = $derived(holdAdvisory.ordered.slice(1));
  const primaryPriorityMeta = $derived(primaryHold ? getNotePriorityMeta(primaryHold.priority) : null);
  const primaryHoldDisplayName = $derived(primaryHold ? resolveDisplayName(primaryHold) : '');
  const priorityNudge = $derived(primaryHold?.priority === 'urgent' ? '!!' : primaryHold?.priority === 'high' ? '!' : '');
  let secondaryHoldsOpen = $state(false);
  let secondaryPopoverReady = $state(false);
  let secondaryMoreButton = $state<HTMLButtonElement | undefined>();
  let secondaryPopover = $state<HTMLDivElement | undefined>();
  let secondaryPopoverLeft = $state(0);
  let secondaryPopoverTop = $state(0);
  let secondaryPopoverFrame: number | null = null;
  const secondaryHoldAriaLabel = $derived.by(() =>
    secondaryHolds
      .map((note) => {
        const meta = getNotePriorityMeta(note.priority);
        return `${resolveDisplayName(note)}, ${meta.label}`;
      })
      .join(', ')
  );

  function getPriorityNudge(priority: Note['priority']): string {
    return priority === 'urgent' ? '!!' : priority === 'high' ? '!' : '';
  }

  function cancelSecondaryPopoverFrame(): void {
    if (secondaryPopoverFrame === null) return;
    cancelAnimationFrame(secondaryPopoverFrame);
    secondaryPopoverFrame = null;
  }

  function openSecondaryHolds(): void {
    if (!secondaryMoreButton || !secondaryPopover || secondaryHolds.length === 0) return;

    secondaryHoldsOpen = true;
    secondaryPopoverReady = false;
    if (typeof secondaryPopover.showPopover === 'function' && !secondaryPopover.matches(':popover-open')) {
      secondaryPopover.showPopover();
    }

    cancelSecondaryPopoverFrame();
    secondaryPopoverFrame = requestAnimationFrame(() => {
      if (!secondaryHoldsOpen || !secondaryMoreButton || !secondaryPopover) return;

      const triggerRect = secondaryMoreButton.getBoundingClientRect();
      const popoverRect = secondaryPopover.getBoundingClientRect();
      const gutter = 8;
      const maxLeft = Math.max(gutter, window.innerWidth - popoverRect.width - gutter);
      const preferredLeft = triggerRect.right - popoverRect.width;
      const preferredTop = triggerRect.top - popoverRect.height - gutter;

      secondaryPopoverLeft = Math.min(Math.max(gutter, preferredLeft), maxLeft);
      const maxTop = Math.max(gutter, window.innerHeight - popoverRect.height - gutter);
      secondaryPopoverTop = preferredTop >= gutter
        ? preferredTop
        : Math.min(triggerRect.bottom + gutter, maxTop);
      secondaryPopoverReady = true;
      secondaryPopoverFrame = null;
    });
  }

  function closeSecondaryHolds(): void {
    secondaryHoldsOpen = false;
    secondaryPopoverReady = false;
    cancelSecondaryPopoverFrame();
    if (secondaryPopover && typeof secondaryPopover.hidePopover === 'function' && secondaryPopover.matches(':popover-open')) {
      secondaryPopover.hidePopover();
    }
  }

  function handleSecondaryHoldsKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Escape') return;
    closeSecondaryHolds();
    secondaryMoreButton?.blur();
  }

  onDestroy(cancelSecondaryPopoverFrame);
  const compactHoldAriaText = $derived.by(() => {
    if (!primaryHold) return '';

    const summary = [`HOLD ${primaryHoldDisplayName}`];
    if (primaryHold.priority !== 'normal' && primaryPriorityMeta) {
      summary.push(primaryPriorityMeta.label);
    }
    if (holdAdvisory.secondarySummary) {
      summary.push(holdAdvisory.secondarySummary);
    }
    return summary.join(', ');
  });
  const holdAriaDetail = $derived(compactHoldAriaText ? `, advisory ${compactHoldAriaText}` : '');
  const gpuAriaLabel = $derived.by(() => {
    const usage = displayUsers.length > 0 ? displayUsers.join(', ') : 'idle';
    return `GPU ${gpu.index}, users ${usage}, utilization ${utilValue} percent, memory ${memUsedGB} of ${memTotalGB} gigabytes${holdAriaDetail}`;
  });

</script>

<div class="monitor-gpu-row" role="group" data-state={availabilityState} aria-label={gpuAriaLabel}>
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
        <span
          class={`monitor-gpu-row__hold-cue ${primaryPriorityMeta?.className ?? ''}`}
        >
          <span class="monitor-gpu-row__hold-kind" aria-hidden="true">HOLD</span>
          <span class="monitor-gpu-row__hold-owner" aria-hidden="true">{primaryHoldDisplayName}</span>
          {#if priorityNudge}
            <span class="monitor-gpu-row__hold-nudge" aria-hidden="true">{priorityNudge}</span>
          {/if}
          {#if holdAdvisory.secondarySummary}
            <span class="monitor-gpu-row__hold-more-wrap">
              <button
                bind:this={secondaryMoreButton}
                type="button"
                class="monitor-gpu-row__hold-more"
                aria-label={`다른 HOLD ${holdAdvisory.secondaryCount}개: ${secondaryHoldAriaLabel}`}
                onmouseenter={openSecondaryHolds}
                onmouseleave={closeSecondaryHolds}
                onfocus={openSecondaryHolds}
                onblur={closeSecondaryHolds}
                onkeydown={handleSecondaryHoldsKeydown}
              >{holdAdvisory.secondarySummary}</button>
              <div
                bind:this={secondaryPopover}
                class="monitor-gpu-row__hold-popover"
                popover="manual"
                role="tooltip"
                data-open={secondaryHoldsOpen && secondaryPopoverReady ? 'true' : 'false'}
                style={`--hold-popover-left: ${secondaryPopoverLeft}px; --hold-popover-top: ${secondaryPopoverTop}px;`}
              >
                {#each secondaryHolds as note (note.id)}
                  {@const meta = getNotePriorityMeta(note.priority)}
                  {@const nudge = getPriorityNudge(note.priority)}
                  <span class={`monitor-gpu-row__hold-popover-entry ${meta.className}`}>
                    <span class="monitor-gpu-row__hold-popover-owner">{resolveDisplayName(note)}</span>
                    {#if nudge}
                      <span class="monitor-gpu-row__hold-popover-nudge" aria-label={meta.label}>{nudge}</span>
                    {/if}
                  </span>
                {/each}
              </div>
            </span>
          {/if}
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
