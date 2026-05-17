/**
 * SafeMAPS — AQI Trend Hook
 *
 * Fetches the 7-day average AQI from the backend for a given lat/lon
 * and compares it to today's AQI to compute a trend badge:
 *   "Today's AQI 22% higher than your 7-day avg"
 *   "Air quality better than usual today"
 *
 * Falls back gracefully if backend is offline or no history is available.
 *
 * Usage:
 *   const { trend, loading } = useAQITrend(lat, lon);
 *   // trend: { todayAQI, avgAQI, pctChange, label, direction }
 */

import { useState, useEffect } from 'react';

const API_BASE = '/api';

export function useAQITrend(lat, lon) {
    const [trend,   setTrend]   = useState(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!lat || !lon) return;
        let cancelled = false;

        const fetchTrend = async () => {
            setLoading(true);
            try {
                // Fetch recent AQI readings near this location
                const params = new URLSearchParams({
                    min_lat: (+lat - 0.02).toFixed(4),
                    max_lat: (+lat + 0.02).toFixed(4),
                    min_lon: (+lon - 0.02).toFixed(4),
                    max_lon: (+lon + 0.02).toFixed(4),
                });
                const resp = await fetch(`${API_BASE}/aqi/heatmap?${params}`, {
                    signal: AbortSignal.timeout(4000),
                });
                if (!resp.ok || cancelled) return;

                const data = await resp.json();
                const readings = data?.features ?? [];
                if (readings.length === 0) return;

                // Today's AQI: average of all points in bbox
                const todayValues = readings
                    .map(f => f.properties?.aqi)
                    .filter(v => v != null && v > 0);

                if (todayValues.length === 0) return;
                const todayAQI = todayValues.reduce((a, b) => a + b, 0) / todayValues.length;

                // Attempt 7-day history
                const histResp = await fetch(`${API_BASE}/aqi/history?${params}&days=7`, {
                    signal: AbortSignal.timeout(4000),
                }).catch(() => null);

                let avgAQI = null;
                if (histResp?.ok) {
                    const hist = await histResp.json().catch(() => null);
                    const histValues = (hist?.readings ?? [])
                        .map(r => r.aqi)
                        .filter(v => v != null && v > 0);
                    if (histValues.length >= 5) {
                        avgAQI = histValues.reduce((a, b) => a + b, 0) / histValues.length;
                    }
                }

                if (!cancelled && avgAQI) {
                    const pctChange = ((todayAQI - avgAQI) / avgAQI) * 100;
                    const direction = pctChange > 5 ? 'worse' : pctChange < -5 ? 'better' : 'same';
                    const absPct = Math.abs(pctChange).toFixed(0);
                    const label = direction === 'worse'
                        ? `AQI ${absPct}% higher than 7-day avg`
                        : direction === 'better'
                        ? `AQI ${absPct}% lower than usual`
                        : 'AQI similar to recent days';

                    setTrend({
                        todayAQI:  Math.round(todayAQI),
                        avgAQI:    Math.round(avgAQI),
                        pctChange: +pctChange.toFixed(1),
                        direction,
                        label,
                    });
                }
            } catch {
                // Silently ignore — trend is informational only
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        fetchTrend();
        return () => { cancelled = true; };
    }, [lat, lon]);

    return { trend, loading };
}
