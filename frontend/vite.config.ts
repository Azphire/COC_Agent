import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
const backendPort = process.env.COC_BACKEND_PORT || process.env.APP_PORT || '8000'
const backendHost = process.env.COC_BACKEND_HOST || '127.0.0.1'
export default defineConfig({
  plugins: [react()],
  server: {
    host: process.env.COC_FRONTEND_HOST || '127.0.0.1', port: Number(process.env.COC_FRONTEND_PORT || 5173), strictPort: true,
    proxy: {
      '/api': { target: `http://${backendHost}:${backendPort}` },
      '/ws': { target: `ws://${backendHost}:${backendPort}`, ws: true },
    },
  },
})
