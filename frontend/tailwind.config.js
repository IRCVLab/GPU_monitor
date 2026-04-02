/** @type {import('tailwindcss').Config} */
export default {
	content: ['./src/**/*.{html,js,svelte,ts}'],
	darkMode: 'class',
	theme: {
		extend: {
			colors: {
				surface: {
					DEFAULT: '#1a1a1a',
					card: '#242424',
					hover: '#2a2a2a',
					border: '#333333'
				}
			},
			fontFamily: {
				sans: ['-apple-system', 'BlinkMacSystemFont', 'SF Pro Display', 'Segoe UI', 'sans-serif'],
				mono: ['SF Mono', 'JetBrains Mono', 'Fira Code', 'monospace']
			}
		}
	},
	plugins: []
};
