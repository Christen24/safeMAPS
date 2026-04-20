import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        // Proxy all /api calls to the FastAPI backend during development.
        // This eliminates the hardcoded localhost:8000 URL in App.jsx and
        // avoids CORS issues — the browser talks to Vite, Vite forwards to FastAPI.
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
                // Remove /api prefix before forwarding:
                // /api/route → http://localhost:8000/api/route
                // (backend already includes /api in its router prefix, so no rewrite needed)
            },
        },
    },
    build: {
        outDir: 'dist',
        // Sourcemaps help debugging the production build
        sourcemap: false,
    },
})
