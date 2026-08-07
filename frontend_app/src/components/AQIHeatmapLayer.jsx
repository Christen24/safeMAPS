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
 * Draw one grid cell as an axis-aligned rectangle, given its center and
 * the (lat, lon) half-widths of a cell. Cells are uniform rectangles on
 * a fixed grid, so this is all the geometry we ever need — no GeoJSON
 * polygon coordinates required from the backend.
 * We call map.latLngToLayerPoint to stay in the layer-pane coordinate
 * space, which is consistent with the canvas position set via
 * L.DomUtil.setPosition.
 */
function drawCell(ctx, centerLat, centerLon, halfLat, halfLon, map) {
    const corners = [
        [centerLat - halfLat, centerLon - halfLon],
        [centerLat - halfLat, centerLon + halfLon],
        [centerLat + halfLat, centerLon + halfLon],
        [centerLat + halfLat, centerLon - halfLon],
    ];
    ctx.beginPath();
    corners.forEach(([lat, lon], idx) => {
        const pt = map.latLngToLayerPoint([lat, lon]);
        if (idx === 0) ctx.moveTo(pt.x, pt.y);
        else ctx.lineTo(pt.x, pt.y);
    });
    ctx.closePath();
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

            // Half-widths of a cell in degrees — same for every feature
            // in a given response, so computed once per draw rather than
            // per cell. Falls back to the pre-optimization ~100m grid
            // size if an older cached response predates this field.
            const stepLat = aqiData?.metadata?.cell_step_lat ?? 0.0009;
            const stepLon = aqiData?.metadata?.cell_step_lon ?? 0.00098;
            const halfLat = stepLat / 2;
            const halfLon = stepLon / 2;

            ctx.lineJoin = 'round';
            ctx.lineWidth = 0.6;

            for (const feature of features) {
                const p = feature.properties;
                if (!p || p.center_lat == null || p.center_lon == null) continue;

                ctx.fillStyle   = aqiFill(p.aqi);
                ctx.strokeStyle = aqiStroke(p.aqi);
                drawCell(ctx, p.center_lat, p.center_lon, halfLat, halfLon, map);
                ctx.fill();
                ctx.stroke();
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
