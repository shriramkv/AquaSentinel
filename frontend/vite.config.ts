import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = `http://localhost:${process.env.BACKEND_PORT ?? 8080}`

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/ws': { target: backend, ws: true },
    },
  },
})
