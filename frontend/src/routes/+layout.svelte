<script lang="ts">
	import '../app.css';
	import { browser } from '$app/environment';
	import { connectWs, disconnectWs } from '$lib/ws';

	function initLayoutRuntime() {
		if (!browser) return;

		const runtime = globalThis as typeof globalThis & {
			__monitoringV2LayoutCleanup?: () => void;
		};
		runtime.__monitoringV2LayoutCleanup?.();

		try {
			connectWs();
		} catch { /* ignore */ }

		runtime.__monitoringV2LayoutCleanup = () => {
			disconnectWs();
		};
	}

	initLayoutRuntime();
</script>

<slot />
