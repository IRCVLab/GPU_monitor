<script lang="ts">
	let {
		cycleKey,
		durationMs,
		attention = false,
		variant = 'header'
	}: {
		cycleKey: number;
		durationMs: number;
		attention?: boolean;
		variant?: 'header' | 'floating';
	} = $props();

	const ringStyle = $derived(`--ops-refresh-duration: ${Math.max(800, durationMs)}ms;`);
</script>

<span
	class="ops-refresh-ring"
	class:attention={attention}
	data-variant={variant}
	style={ringStyle}
	aria-hidden="true"
>
	<svg class="ops-refresh-ring__svg" viewBox="0 0 24 24">
		<circle class="ops-refresh-ring__track" pathLength="1" cx="12" cy="12" r="9.25"></circle>
		{#key cycleKey}
			<circle class="ops-refresh-ring__progress" pathLength="1" cx="12" cy="12" r="9.25"></circle>
		{/key}
	</svg>
	<span class="ops-refresh-ring__dot"></span>
</span>

