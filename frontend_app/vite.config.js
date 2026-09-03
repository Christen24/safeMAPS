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
            injectRegister: 'auto',
            workbox: {
                cleanupOutdatedCaches: true,
                clientsClaim: true,
                skipWaiting: true,
                // Cache the shell (HTML, JS, CSS) and leaflet tiles
                globPatterns: ['**/*.{js,css,html,png,svg,ico}'],
                runtimeCaching: [
                    {
                        // Cache Leaflet tile requests for offline map viewing
                        urlPattern: /^https:\/\/server\.arcgisonline\.com\/ArcGIS\/rest\/services\/World_Imagery\/MapServer\/tile\/.*/i,
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
                            // Bug fix: without this, NetworkFirst caches ANY
                            // response fetch() resolves -- including 404/422
                            // error bodies, since a bad HTTP status still
                            // counts as a "successful" fetch. That meant a
                            // route that failed once (e.g. during the bbox/
                            // graph issues) stayed cached as a false failure
                            // for up to an hour even after the backend was
                            // fixed. Only cache genuine 200s.
                            cacheableResponse: { statuses: [0, 200] },
                            networkTimeoutSeconds: 5,
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
        allowedHosts: ['safemaps-frontend.onrender.com'],
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
            },
        },
    },
    preview: {
        host: '0.0.0.0',
        allowedHosts: ['safemaps-frontend.onrender.com'],
    },
    build: {
        outDir: 'dist',
        sourcemap: false,
        rollupOptions: {
            output: {
                // Split the heavy leaflet/react-leaflet bundle out for better
                // caching. (A separate 'react' split used to live here too,
                // but Rollup was always inlining react/react-dom into
                // whichever chunk imported them first, leaving this one an
                // empty 0kB file that still cost a network round trip.)
                manualChunks: {
                    leaflet: ['leaflet', 'react-leaflet'],
                },
            },
        },
    },
})