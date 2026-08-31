import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 8080,
    // In dev the SPA is served by Vite and the API by uvicorn. Proxying /v1
    // here keeps them on one origin, so the session cookie behaves exactly as
    // it will behind nginx in staging and production.
    proxy: {
      '/v1': {
        target: 'http://api:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
