import { useEffect, useState } from 'react';
import { GeoJSON, MapContainer, Marker, Polyline, TileLayer, ZoomControl, useMap } from 'react-leaflet';
import L from 'leaflet';

// ── Fix default Leaflet icon paths ───────────────────────────
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
    iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
    iconUrl:       'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl:     'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

// ── Marker icons matching the primary map style ───────────────
function makeIcon(ring, fill) {
    return L.divIcon({
        className: '',
        html: `
          <svg width="22" height="32" viewBox="0 0 22 32" xmlns="http://www.w3.org/2000/svg">
            <ellipse cx="11" cy="26" rx="5.5" ry="2" fill="#03050a" opacity="0.4"/>
            <circle cx="11" cy="11" r="9" fill="none" stroke="${ring}" stroke-width="1.5" opacity="0.4"/>
            <circle cx="11" cy="11" r="4"  fill="${fill}" />
            <line x1="11" y1="2"  x2="11" y2="6"  stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="11" y1="16" x2="11" y2="20" stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="2"  y1="11" x2="6"  y2="11" stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="16" y1="11" x2="20" y2="11" stroke="${ring}" stroke-width="1" opacity="0.5"/>
          </svg>`,
        iconSize:   [22, 32],
        iconAnchor: [11, 11],
    });
}
const originIcon = makeIcon('#4ecb8d', '#4ecb8d');
const destIcon   = makeIcon('#f16565', '#f16565');

// ── Profile colours — same as primary MapView ─────────────────
const PROFILE_COLORS = {
    fastest:    '#4fc3e0',
    safest:     '#4ecb8d',
    healthiest: '#f0a93e',
    balanced:   '#9b87e8',
};

function FitRoute({ geometries }) {
    const map = useMap();
    useEffect(() => {
        const coords = geometries
            .flatMap(geom => geom?.coordinates || [])
            .filter(pt => Array.isArray(pt) && pt.length >= 2)
            .map(([lon, lat]) => [lat, lon]);
        if (coords.length) {
            map.fitBounds(coords, { padding: [32, 32], maxZoom: 13 });
        }
    }, [geometries, map]);
    return null;
}

// ── Route with shadow + casing (matches primary map treatment) ─
function RouteLayer({ routes }) {
    return (
        <>
            {routes.map((route, i) => {
                const profile = route.profile || route.route_id || 'balanced';
                const color = PROFILE_COLORS[profile] || '#9b87e8';
                const coords = (route.geometry?.coordinates || []).map(([lon, lat]) => [lat, lon]);
                if (coords.length < 2) return null;
                const key = `${profile}-${i}`;
                return (
                    <span key={key}>
                        {/* shadow */}
                        <Polyline positions={coords} pathOptions={{ color: '#03050a', weight: 9,   opacity: 0.32, lineCap: 'round', lineJoin: 'round', interactive: false }} />
                        {/* casing */}
                        <Polyline positions={coords} pathOptions={{ color: '#eef1fb', weight: 7.5, opacity: 0.88, lineCap: 'round', lineJoin: 'round', interactive: false }} />
                        {/* fill */}
                        <Polyline positions={coords} pathOptions={{ color,            weight: 4.5, opacity: 0.98, lineCap: 'round', lineJoin: 'round' }} />
                    </span>
                );
            })}
        </>
    );
}

// ── Origin/destination markers extracted from route geometry ──
function RouteMarkers({ routes }) {
    if (!routes.length) return null;
    const allCoords = routes.flatMap(r => r.geometry?.coordinates || []);
    if (allCoords.length < 2) return null;
    const [oLon, oLat] = allCoords[0];
    const [dLon, dLat] = allCoords[allCoords.length - 1];
    return (
        <>
            <Marker position={[oLat, oLon]} icon={originIcon} />
            <Marker position={[dLat, dLon]} icon={destIcon}   />
        </>
    );
}

// ── Basemap tiles ─────────────────────────────────────────────
const TILE_LAYERS = {
    satellite: {
        url:         'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attribution: 'Tiles &copy; Esri &mdash; Esri, Maxar, Earthstar Geographics',
        maxZoom:     19,
    },
    labels: {
        url:     'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
        maxZoom: 19,
        opacity: 0.85,
    },
    streets: {
        url:         'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom:     19,
    },
};

export default function AIMap({ routes }) {
    const [basemap, setBasemap] = useState('satellite');
    const geometries = routes.map(r => r.geometry).filter(Boolean);

    return (
        <div className="ai-map">
            <MapContainer center={[12.9716, 77.5946]} zoom={11} zoomControl={false}>
                {basemap === 'satellite' ? (
                    <>
                        <TileLayer {...TILE_LAYERS.satellite} />
                        <TileLayer {...TILE_LAYERS.labels} />
                    </>
                ) : (
                    <TileLayer {...TILE_LAYERS.streets} />
                )}

                <ZoomControl position="bottomright" />
                <FitRoute geometries={geometries} />
                <RouteLayer routes={routes} />
                <RouteMarkers routes={routes} />
            </MapContainer>

            {/* Compact basemap toggle */}
            <div className="ai-basemap-toggle">
                <button
                    className={basemap === 'satellite' ? 'active' : ''}
                    onClick={() => setBasemap('satellite')}
                >
                    Satellite
                </button>
                <button
                    className={basemap === 'streets' ? 'active' : ''}
                    onClick={() => setBasemap('streets')}
                >
                    Streets
                </button>
            </div>

            {!routes.length && (
                <div className="ai-map-empty">
                    <span>Route geometry will appear here once SafeMAPS computes a path.</span>
                </div>
            )}
        </div>
    );
}
