import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Development only (`npm run dev`): send API calls and photos to the
  // backend, so the dashboard can call "/api" exactly as it does when the
  // backend serves it.
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/media": "http://localhost:8000",
    },
  },
})
