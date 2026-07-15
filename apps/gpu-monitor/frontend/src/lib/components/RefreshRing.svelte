<script lang="ts">
	let {
		attention = false,
		variant = 'header'
	}: {
		attention?: boolean;
		variant?: 'header' | 'floating';
	} = $props();
</script>

<span
	class="ops-refresh-ring"
	class:attention={attention}
	data-variant={variant}
	aria-hidden="true"
>
	<svg class="ops-refresh-ring__svg" viewBox="0 0 24 24">
		<circle class="ops-refresh-ring__track" pathLength="1" cx="12" cy="12" r="9.25"></circle>
		<circle class="ops-refresh-ring__marker" cx="12" cy="2.75" r="0.75"></circle>
		<circle class="ops-refresh-ring__satellite" cx="12" cy="2.75" r="1.35"></circle>
	</svg>
	<span class="ops-refresh-ring__dot"></span>
</span>
