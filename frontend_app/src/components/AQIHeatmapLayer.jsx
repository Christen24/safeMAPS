import { useEffect, useRef } from 'react';
import { useMap } from 'react-leaflet';
import L from 'leaflet';

// ── AQI colour scale (matches MapView aqiColor) ───────────────────────
const STOPS = [
    [0,   [78,  203, 141]],   // Good — #4ecb8d
    [50,  [240, 169,  62]],   // Moderate — #f0a93e
    [100, [230, 140,  40]],   // USG — #e68c28
    [150, [241, 101, 101]],   // Unhealthy — #f16565
    [200, [155, 135, 232]],   // Very unhealthy — #9b87e8
    [300, [123,  60, 168]],   // Hazardous — #7b3ca8
    [500, [80,   30, 110]],
];

function aqiToRGB(aqi) {
    const v = Math.max(0, Math.min(500, Number(aqi) || 0));
    let lo = STOPS[0], hi = STOPS[STOPS.length - 1];
    for (let i = 1; i < STOPS.length; i++) {
        if (v <= STOPS[i][0]) { lo = STOPS[i - 1]; hi = STOPS[i]; break; }
    }
    const span = hi[0] - lo[0] || 1;
    const t = (v - lo[0]) / span;
    return lo[1].map((c, i) => Math.round(c + (hi[1][i] - c) * t));
}

function aqiFill(aqi, alpha = 0.52) {
    const [r, g, b] = aqiToRGB(aqi);
    return `rgba(${r},${g},${b},${alpha})`;
}

function aqiStroke(aqi, alpha = 0.18) {
    const [r, g, b] = aqiToRGB(aqi);
    return `rgba(${r},${g},${b},${alpha})`;
}

/**
 * Draw a GeoJSON Polygon ring list onto ctx.
 * ring = [[lon, lat], [lon, lat], ...]
 * We call map.latLngToLayerPoint to stay in the layer-pane coordinate space,
 * which is consistent with the canvas position set via L.DomUtil.setPosition.
 */
function drawRing(ctx, ring, map) {
    if (!ring || ring.length < 2) return false;
    ctx.beginPath();
    ring.forEach(([lon, lat], idx) => {
        const pt = map.latLngToLayerPoint([lat, lon]);
        if (idx === 0) ctx.moveTo(pt.x, pt.y);
        else ctx.lineTo(pt.x, pt.y);
    });
    ctx.closePath();
    return true;
}

export default function AQIHeatmapLayer({ aqiData }) {
    const map = useMap();
    const canvasRef = useRef(null);
    const frameRef = useRef(null);

    // ── Mount canvas into the overlay pane once ───────────────────────
    useEffect(() => {
        const canvas = L.DomUtil.create('canvas', 'aqi-canvas-layer');
        canvasRef.current = canvas;
        canvas.style.cssText = [
            'position:absolute',
            'pointer-events:none',
            'z-index:320',
            'opacity:0.88',
            // No mixBlendMode — 'multiply' kills colours on dark satellite tiles.
            // No element-level blur — blurs crisp cell boundaries and bleeds colours.
        ].join(';');
        map.getPanes().overlayPane.appendChild(canvas);

        return () => {
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
            canvas.remove();
            canvasRef.current = null;
        };
    }, [map]);

    // ── Redraw on data or map view change ─────────────────────────────
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const draw = () => {
            const size   = map.getSize();
            const dpr    = window.devicePixelRatio || 1;

            // Position the canvas at the layer-pane origin.
            // latLngToLayerPoint uses the same coordinate space, so cells land exactly.
            const topLeft = map.containerPointToLayerPoint([0, 0]);
            L.DomUtil.setPosition(canvas, topLeft);

            canvas.width  = size.x * dpr;
            canvas.height = size.y * dpr;
            canvas.style.width  = `${size.x}px`;
            canvas.style.height = `${size.y}px`;

            const ctx = canvas.getContext('2d');
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, size.x, size.y);

            const features = aqiData?.features;
            if (!features?.length) return;

            ctx.lineJoin = 'round';
            ctx.lineWidth = 0.6;

            for (const feature of features) {
                const aqi      = feature.properties?.aqi;
                const geom     = feature.geometry;
                if (!geom) continue;

                ctx.fillStyle   = aqiFill(aqi);
                ctx.strokeStyle = aqiStroke(aqi);

                if (geom.type === 'Polygon') {
                    // coordinates = [outerRing, ...holes]
                    // outer ring = [[lon, lat], ...]
                    const outer = geom.coordinates?.[0];
                    if (drawRing(ctx, outer, map)) {
                        ctx.fill();
                        ctx.stroke();
                    }
                } else if (geom.type === 'MultiPolygon') {
                    // coordinates = [polygon, ...] where polygon = [outerRing, ...holes]
                    for (const polygon of geom.coordinates) {
                        const outer = polygon?.[0];
                        if (drawRing(ctx, outer, map)) {
                            ctx.fill();
                            ctx.stroke();
                        }
                    }
                }
            }
        };

        const schedule = () => {
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
            frameRef.current = requestAnimationFrame(draw);
        };

        schedule();
        map.on('move zoom resize viewreset zoomend moveend', schedule);
        return () => {
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
            map.off('move zoom resize viewreset zoomend moveend', schedule);
        };
    }, [aqiData, map]);

    return null;
}
