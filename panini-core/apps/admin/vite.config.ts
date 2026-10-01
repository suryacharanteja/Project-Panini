import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Built into the console's public folder: the gateway serves it at / (assets under /admin/),
// and packaging already copies frontend/public, so no new runtime wiring is needed.
export default defineConfig({
  base: '/admin/',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../../../frontend/public/admin',
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  },
  server: {
    host: '127.0.0.1',
    port: 3240,
    proxy: { '/api': 'http://127.0.0.1:3200', '/auth': 'http://127.0.0.1:3200' },
  },
});
