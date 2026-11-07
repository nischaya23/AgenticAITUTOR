import { defineConfig } from 'vite';

export default defineConfig({
  root: 'frontend',
  esbuild: { jsx: 'automatic' },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
});
