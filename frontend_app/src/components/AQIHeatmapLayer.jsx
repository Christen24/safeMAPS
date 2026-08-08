import { useEffect, useRef } from 'react';
import { useMap } from 'react-leaflet';
import L from 'leaflet';

// TEMP DIAGNOSTIC — matches App.jsx/MapView.jsx's DEBUG_AQI. Remove
// together once the "cornering" report is root-caused.
const DEBUG_AQI = true;

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

// Bumped from 0.52 — the blur pass (below) softens perceived saturation
// by spreading each cell's colour into its neighbours, so the pre-blur
// fill needs to run a bit richer to land at a similar final intensity.
function aqiFill(aqi, alpha = 0.62) {
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

/**
 * 1 in the interior of the fetched bbox, ramping linearly down to 0 at
 * the very edge (within the outer `fadeFraction` of the bbox's span).
 * Without this the overlay stops dead at the query boundary — a hard
 * rectangle instead of the soft, edge-to-edge wash Google's overlay has.
 * Combined with padding the fetch itself (see MapView.jsx's emitBounds),
 * this fade zone normally sits outside the visible viewport, so in
 * practice you only see it if you pan right up to the edge of loaded data.
 */
function edgeFadeFactor(lat, lon, bbox, fadeFraction = 0.10) {
    if (!bbox) return 1;
    const [minLon, minLat, maxLon, maxLat] = bbox;
    const lonSpan = (maxLon - minLon) || 1;
    const latSpan = (maxLat - minLat) || 1;
    const distLon = Math.min(lon - minLon, maxLon - lon) / lonSpan;
    const distLat = Math.min(lat - minLat, maxLat - lat) / latSpan;
    const fadeLon = Math.max(0, Math.min(1, distLon / fadeFraction));
    const fadeLat = Math.max(0, Math.min(1, distLat / fadeFraction));
    return Math.min(fadeLon, fadeLat);
}

export default function AQIHeatmapLayer({ aqiData }) {
    const map = useMap();
    const canvasRef = useRef(null);
    // Offscreen buffer: cells are drawn here crisp/unblurred, then
    // composited onto the visible canvas through a single blur filter.
    // One blur of the whole composited image (not one blur per cell)
    // keeps this cheap regardless of cell count — a single GPU-accelerated
    // drawImage call, same cost whether there are 200 cells or 100,000.
    const bufferRef = useRef(null);
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
        ].join(';');
        map.getPanes().overlayPane.appendChild(canvas);

        bufferRef.current = document.createElement('canvas'); // detached, never mounted

        return () => {
            if (frameRef.current) cancelAnimationFrame(frameRef.current);
            canvas.remove();
            canvasRef.current = null;
            bufferRef.current = null;
        };
    }, [map]);

    // ── Redraw on data or map view change ─────────────────────────────
    useEffect(() => {
        const canvas = canvasRef.current;
        const buffer = bufferRef.current;
        if (!canvas || !buffer) return;

        const draw = () => {
            const size = map.getSize();
            const dpr  = window.devicePixelRatio || 1;

            // Position the visible canvas at the layer-pane origin.
            // latLngToLayerPoint uses the same coordinate space, so cells land exactly.
            const topLeft = map.containerPointToLayerPoint([0, 0]);
            L.DomUtil.setPosition(canvas, topLeft);

            canvas.width  = size.x * dpr;
            canvas.height = size.y * dpr;
            canvas.style.width  = `${size.x}px`;
            canvas.style.height = `${size.y}px`;
            buffer.width  = canvas.width;
            buffer.height = canvas.height;

            const ctx = canvas.getContext('2d');
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, size.x, size.y);

            const features = aqiData?.features;
            if (!features?.length) return;

            if (DEBUG_AQI && aqiData?.metadata?.bbox) {
                const [dMinLon, dMinLat, dMaxLon, dMaxLat] = aqiData.metadata.bbox;
                const vb = map.getBounds();
                const covers =
                    dMinLon <= vb.getWest() && dMaxLon >= vb.getEast() &&
                    dMinLat <= vb.getSouth() && dMaxLat >= vb.getNorth();
                console.log('[AQI DEBUG] draw() — data bbox vs visible viewport', {
                    dataBbox: aqiData.metadata.bbox,
                    visibleBbox: [vb.getWest(), vb.getSouth(), vb.getEast(), vb.getNorth()],
                    dataCoversVisibleViewport: covers,
                    featureCount: features.length,
                    zoom: map.getZoom(),
                });
            }

            const bctx = buffer.getContext('2d');
            bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            bctx.clearRect(0, 0, size.x, size.y);

            // Half-widths of a cell in degrees — same for every feature
            // in a given response, so computed once per draw rather than
            // per cell. Falls back to the pre-optimization ~100m grid
            // size if an older cached response predates this field.
            const stepLat = aqiData?.metadata?.cell_step_lat ?? 0.0009;
            const stepLon = aqiData?.metadata?.cell_step_lon ?? 0.00098;
            const halfLat = stepLat / 2;
            const halfLon = stepLon / 2;
            const bbox = aqiData?.metadata?.bbox; // [minLon, minLat, maxLon, maxLat]

            // Blur radius scales with on-screen cell size: enough to melt
            // adjacent cells into a continuous gradient without smearing
            // away real spatial detail when zoomed in close. Estimated
            // from actual projected pixel width of one cell at the
            // current view, not from zoom level directly, since that
            // stays correct regardless of the server-side aggregation
            // factor in play.
            let blurPx = 14;
            if (features[0]?.properties) {
                const p0 = features[0].properties;
                const a = map.latLngToLayerPoint([p0.center_lat, p0.center_lon]);
                const b = map.latLngToLayerPoint([p0.center_lat, p0.center_lon + stepLon]);
                const cellPx = Math.abs(b.x - a.x);
                blurPx = Math.max(8, Math.min(cellPx * 1.15, 46));
            }

            for (const feature of features) {
                const p = feature.properties;
                if (!p || p.center_lat == null || p.center_lon == null) continue;

                const fade = edgeFadeFactor(p.center_lat, p.center_lon, bbox);
                if (fade <= 0) continue;

                bctx.fillStyle = aqiFill(p.aqi, 0.62 * fade);
                drawCell(bctx, p.center_lat, p.center_lon, halfLat, halfLon, map);
                bctx.fill();
            }

            // Single blur pass over the whole composited buffer — this is
            // what turns a mosaic of flat-coloured rectangles into the
            // soft, seamless gradient look, and it costs one drawImage
            // call regardless of how many cells were drawn into it.
            ctx.filter = `blur(${blurPx}px)`;
            ctx.drawImage(buffer, 0, 0, size.x, size.y);
            ctx.filter = 'none';
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
