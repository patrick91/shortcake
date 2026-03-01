import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { mockApi } from './mock-api';

const apiOrigin = process.env.SHORTCAKE_API_ORIGIN;
const useMock = !apiOrigin;

export default defineConfig({
  plugins: [tailwindcss(), react(), ...(useMock ? [mockApi()] : [])],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: apiOrigin
      ? {
          '/api': {
            target: apiOrigin,
            changeOrigin: true,
          },
        }
      : undefined,
  },
});
