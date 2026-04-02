<script lang="ts">
  import type { ServerStatus } from '$lib/types';
  export let status: ServerStatus;

  const config: Record<ServerStatus, { dotClass: string; label: string; badgeClass: string }> = {
    online:   { dotClass: 'bg-emerald-400', label: '온라인',    badgeClass: 'badge-online' },
    offline:  { dotClass: 'bg-red-400',     label: '오프라인',  badgeClass: 'badge-offline' },
    degraded: { dotClass: 'bg-yellow-400',  label: '일부 오류', badgeClass: 'badge-degraded' },
    unknown:  { dotClass: 'bg-white/20',    label: '연결 중...', badgeClass: 'badge-unknown' },
  };

  $: c = config[status] ?? config.unknown;
</script>

<span class={c.badgeClass}>
  <span class="w-1.5 h-1.5 rounded-full inline-block {c.dotClass}"></span>
  {c.label}
</span>
