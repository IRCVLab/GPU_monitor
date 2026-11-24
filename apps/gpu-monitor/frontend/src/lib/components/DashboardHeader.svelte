<script lang="ts">
 import type { Snippet } from 'svelte';
 let {
  compact=false, shellClass='max-w-7xl mx-auto', surface=$bindable<HTMLElement|null>(null),
  identity, networkControls, actions, navigationLabel='네트워크 필터'
 }: {
  compact?: boolean;
  shellClass?: string;
  surface?: HTMLElement|null;
  navigationLabel?: string;
  identity: Snippet;
  networkControls: Snippet;
  actions: Snippet;
 } = $props();
</script>

<header bind:this={surface} class="ops-header border-b border-surface-border" inert={compact} aria-hidden={compact}>
 <div class={`ops-header-inner ${shellClass} px-4 sm:px-6`}>
  <div class="ops-identity">{@render identity()}</div>
  <nav class="ops-network ops-network-desktop" aria-label={navigationLabel}>{@render networkControls()}</nav>
  <div class="ops-actions">{@render actions()}</div>
  <nav class="ops-network ops-network-mobile" aria-label={navigationLabel}>{@render networkControls()}</nav>
 </div>
</header>
