import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
    plugins: [
        react(),
        VitePWA({
            registerType: 'autoUpdate',
            includeAssets: ['icons/*.png', 'manifest.json'],
            manifest: false,             // use public/manifest.json
            workbox: {
                // Cache the shell (HTML, JS, CSS) and leaflet tiles
                globPatterns: ['**/*.{js,css,html,png,svg,ico}'],
                runtimeCaching: [
                    {
                        // Cache Leaflet tile requests for offline map viewing
                        urlPattern: /^https:\/\/[a-c]\.tile\.openstreetmap\.org\/.*/i,
                        handler: 'CacheFirst',
                        options: {
                            cacheName: 'osm-tiles',
                            expiration: {
                                maxEntries: 500,
                                maxAgeSeconds: 7 * 24 * 60 * 60, // 1 week
                            },
                            cacheableResponse: { statuses: [0, 200] },
                        },
                    },
                    {
                        // Cache API health endpoint for quick offline detection
                        urlPattern: /\/health$/,
                        handler: 'NetworkFirst',
                        options: {
                            cacheName: 'api-health',
                            networkTimeoutSeconds: 3,
                        },
                    },
                    {
                        // Cache last computed route response for offline fallback
                        urlPattern: /\/api\/route\/.*/,
                        handler: 'NetworkFirst',
                        options: {
                            cacheName: 'api-routes',
                            expiration: { maxEntries: 5, maxAgeSeconds: 3600 },
                        },
                    },
                ],
            },
            devOptions: {
                enabled: true,           // Show PWA in dev mode for testing
            },
        }),
    ],
    server: {
        port: 5173,
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: 'dist',
        sourcemap: false,
        rollupOptions: {
            output: {
                // Split large chunks for better caching
                manualChunks: {
                    leaflet: ['leaflet', 'react-leaflet'],
                    react:   ['react', 'react-dom'],
                },
            },
        },
    },
})
