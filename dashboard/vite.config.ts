import { defineConfig } from 'vite'

export default defineConfig({
  server: { port: 5174, proxy: { '/api': 'http://localhost:3030' } },
  build: { outDir: 'dist' },
})
