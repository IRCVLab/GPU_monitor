import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const apiTarget = process.env.MONITORING_API_TARGET || 'http://127.0.0.1:8001';
const wsTarget =
	process.env.MONITORING_WS_TARGET ||
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
	server: {
		proxy
	},
	preview: {
		proxy
	}
});
