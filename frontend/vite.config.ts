import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {};
const apiTarget = env.MONITORING_API_TARGET || 'http://127.0.0.1:8001';
const wsTarget =
	env.MONITORING_WS_TARGET ||
	apiTarget.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');

const proxy = {
	'/api': {
		target: apiTarget,
		changeOrigin: true,
		rewrite: (path: string) => path.replace(/^\/api/, '')
	},
	'/ws': {
		target: wsTarget,
		ws: true
	}
};

export default defineConfig({
	plugins: [sveltekit()],
	resolve: {
		// vite-plugin-svelte v4 adds `svelte`; Vite 6 requires retaining its client defaults.
		conditions: ['svelte', 'browser', 'module', 'development|production']
	},
	server: {
		proxy
	},
	preview: {
		proxy
	}
});
