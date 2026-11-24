<script lang="ts">
 import type {NodeProps} from '@xyflow/svelte';
 import type {RackCanvasNode} from '$lib/power-flow/rackFrames';
 let {data}:NodeProps<RackCanvasNode>=$props();
</script>
<div class="power-rack" style:width="{data.frame.width}px" style:height="{data.frame.height}px"><span>{data.frame.label}</span><small>{data.frame.serverIds.length} servers</small></div>
<style>
 .power-rack{box-sizing:border-box;border:1.5px solid var(--pf-border,#cbd4e0);border-radius:14px;background:color-mix(in srgb,var(--pf-group,#edf2f8) 22%,transparent);pointer-events:none;color:var(--pf-muted,#62718a)}
 span{position:absolute;left:16px;top:10px;font-size:16px;font-weight:700;letter-spacing:.1px}small{position:absolute;right:16px;top:13px;font-size:12px;font-variant-numeric:tabular-nums}
</style>
