import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';

const apiOrigin = process.env.SHORTCAKE_API_ORIGIN ?? 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [tailwindcss(), react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: apiOrigin,
        changeOrigin: true,
      },
    },
  },
});
