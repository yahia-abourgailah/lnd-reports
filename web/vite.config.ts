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
  // `npm run preview` serves the *built* bundle rather than the dev server, and
  // it is the only way to check the thing that actually ships: a route that
  // works under Vite's dev server and 404s from nginx is a real failure mode,
  // and without this proxy the built bundle has no API to talk to and every
  // screen renders its empty state — which looks like it works.
  preview: {
    port: 4173,
    proxy: {
      '/v1': {
        // Through the dev proxy on the host, not the compose network: this runs
        // outside the containers.
        target: 'http://localhost:8080',
        changeOrigin: false,
      },
    },
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
