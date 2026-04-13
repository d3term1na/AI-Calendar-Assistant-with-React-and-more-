import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": "http://localhost:8000",
      "/register": "http://localhost:8000",
      "/me": "http://localhost:8000",
      "/logout": "http://localhost:8000",
      "/refresh": "http://localhost:8000",
      "/events": "http://localhost:8000",
      "/agenda-suggestions": "http://localhost:8000",
      "/scheduling-insights": "http://localhost:8000",
      "/chat": "http://localhost:8000",
    },
  },
})
