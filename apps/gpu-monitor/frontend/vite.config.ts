import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const proxy = {
	'/api': {
		target: 'http://127.0.0.1:8001',
		changeOrigin: true,
		rewrite: (path: string) => path.replace(/^\/api/, '')
	},
	'/ws': {
		target: 'ws://127.0.0.1:8001',
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
