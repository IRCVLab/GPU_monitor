<script lang="ts">
 import {fanDuration} from '$lib/utils/systemTelemetryVisuals';
 export let rpm:number|null|undefined=null;export let fresh=false;export let size=28;export let label='팬';
 $: duration=fanDuration(rpm,fresh);
</script>
<svg width={size} height={size} viewBox="0 0 40 40" class:running={duration!==null} style={`--rotor-duration:${duration??1}s`} role="img" aria-label={`${label} ${rpm??'—'} RPM${!fresh?' · 마지막 값':''}`}>
 <rect x="2" y="2" width="36" height="36" rx="7" fill="none" stroke="currentColor" stroke-opacity=".28"/>
 <g class="rotor"><path d="M20 17C13 8 25 3 28 9c2 4-2 7-6 9M23 20c9-7 14 5 8 8-4 2-7-2-9-6M20 23c7 9-5 14-8 8-2-4 2-7 6-9M17 20C8 27 3 15 9 12c4-2 7 2 9 6" fill="currentColor" fill-opacity=".68"/><circle cx="20" cy="20" r="3" fill="currentColor"/></g>
</svg>
<style>
 svg{display:block;flex-shrink:0;color:var(--fan,var(--sys-fan,#9faebc))}.rotor{transform-origin:20px 20px;animation:fan-turn var(--rotor-duration) linear infinite;animation-play-state:paused}.running .rotor{animation-play-state:running}@keyframes fan-turn{to{transform:rotate(360deg)}}:global(html.light) svg>rect{fill:#edf1f4;stroke:#d0d8df;stroke-opacity:1}:global(html.light) .rotor>path{fill-opacity:.9}@media(prefers-reduced-motion:reduce){.rotor{animation:none!important}}
</style>
