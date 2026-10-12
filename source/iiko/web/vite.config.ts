import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

// SINGLEFILE=1 — собрать весь сайт в один index.html (JS/CSS/ассеты инлайном)
const single = process.env.SINGLEFILE === '1'

// Фронт функционального демо-сайта ходит в живой FastAPI-бэкенд (api.py) на :8000.
// Прокси убирает CORS-боль: /api/* → localhost:8000/api/*
export default defineConfig({
  plugins: [react(), tailwindcss(), ...(single ? [viteSingleFile()] : [])],
  build: single
    ? { assetsInlineLimit: 100_000_000, cssCodeSplit: false, chunkSizeWarningLimit: 100_000 }
    : {},
  server: {
    port: 5180,
    proxy: {
      // ИИ-слой аналитики — отдельный сервис (:8010). Правило выше общего /api.
      '/api/insights': {
        target: 'http://localhost:8010',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    css: false,
  },
})
