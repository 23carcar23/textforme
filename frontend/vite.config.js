import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build output lands inside the Python package so `textforme` can serve it
// from disk with no Node runtime installed.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../src/textforme/webui/dist',
    emptyOutDir: true,
  },
})
