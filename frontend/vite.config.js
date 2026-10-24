import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': process.env.GT_API || 'http://localhost:8888' },
  },
  build: { target: 'es2020', sourcemap: false, chunkSizeWarningLimit: 900 },
})
