import { useEffect, useRef } from 'react';
import {
    MapContainer, TileLayer, Polyline, Marker,
    Popup, CircleMarker, useMapEvents, useMap,
} from 'react-leaflet';
import L from 'leaflet';

// ── Fix default Leaflet icon paths ────────────────────────────
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
    iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
    iconUrl:       'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl:     'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

// ── Custom markers — tactical crosshair style ─────────────────
function makeIcon(ring, fill) {
    return L.divIcon({
        className: '',
        html: `
          <svg width="22" height="22" viewBox="0 0 22 22" xmlns="http://www.w3.org/2000/svg">
            <circle cx="11" cy="11" r="9" fill="none" stroke="${ring}" stroke-width="1.5" opacity="0.4"/>
            <circle cx="11" cy="11" r="4"  fill="${fill}" />
            <line x1="11" y1="2"  x2="11" y2="6"  stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="11" y1="16" x2="11" y2="20" stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="2"  y1="11" x2="6"  y2="11" stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="16" y1="11" x2="20" y2="11" stroke="${ring}" stroke-width="1" opacity="0.5"/>
          </svg>`,
        iconSize:   [22, 22],
        iconAnchor: [11, 11],
    });
}

const originIcon = makeIcon('#00ff88', '#00ff88');
const destIcon   = makeIcon('#ff4560', '#ff4560');

// ── Profile colours ───────────────────────────────────────────
const PROFILE_COLORS = {
    balanced:   '#b06bff',
    fastest:    '#5db8ff',
    safest:     '#00ff88',
    healthiest: '#ffb830',
};

// ── AQI colour scale ──────────────────────────────────────────
export function aqiColor(aqi) {
    if (aqi <= 50)  return '#00ff88';   // Good — acid green
    if (aqi <= 100) return '#ffb830';   // Moderate — amber
    if (aqi <= 150) return '#ff8c00';   // Unhealthy sensitive — dark orange
    if (aqi <= 200) return '#ff4560';   // Unhealthy — infrared
    if (aqi <= 300) return '#b06bff';   // Very unhealthy — violet
    return '#7b1fa2';                   // Hazardous — deep purple
}

// ── Segment colour runs ───────────────────────────────────────
function buildColoredSegments(segments) {
    if (!segments || segments.length === 0) return [];

    const runs = [];
    let currentColor = null;
    let currentCoords = [];

    for (const seg of segments) {
        const coords = seg.geometry?.coordinates;
        if (!coords || coords.length === 0) continue;

        const color = aqiColor(seg.aqi_value);

        if (color !== currentColor) {
            if (currentCoords.length > 0) {
                runs.push({ color: currentColor, coords: currentCoords });
                currentCoords = [currentCoords[currentCoords.length - 1]];
            }
            currentColor = color;
        }

        const leafletCoords = coords.map(([lon, lat]) => [lat, lon]);
        if (currentCoords.length > 0) {
            currentCoords.push(...leafletCoords.slice(1));
        } else {
            currentCoords.push(...leafletCoords);
        }
    }

    if (currentCoords.length > 0 && currentColor) {
        runs.push({ color: currentColor, coords: currentCoords });
    }

    return runs;
}

// ── Debounced map events ──────────────────────────────────────
function MapEvents({ onMapClick, onBoundsChange }) {
    const debounceRef = useRef(null);

    const map = useMapEvents({
        click(e) { onMapClick(e.latlng); },
        moveend() {
            if (debounceRef.current) clearTimeout(debounceRef.current);
            debounceRef.current = setTimeout(() => {
                const b = map.getBounds();
                onBoundsChange({
                    north: b.getNorth(), south: b.getSouth(),
                    east:  b.getEast(),  west:  b.getWest(),
                });
            }, 500);
        },
    });
    return null;
}

// ── Auto-fit to selected route ────────────────────────────────
function FitBounds({ route }) {
    const map = useMap();
    useEffect(() => {
        if (route?.geometry?.coordinates?.length > 0) {
            const ll = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
            map.fitBounds(L.latLngBounds(ll), { padding: [48, 48] });
        }
    }, [route, map]);
    return null;
}

// ── Selected route — segment-coloured or flat ─────────────────
function SelectedRoute({ route }) {
    const hasSegments = route?.segments?.length > 0;

    if (hasSegments) {
        const runs = buildColoredSegments(route.segments);
        return (
            <>
                {runs.map((run, i) => (
                    <Polyline
                        key={`run-${i}`}
                        positions={run.coords}
                        pathOptions={{
                            color:    run.color,
                            weight:   5,
                            opacity:  0.9,
                            lineCap:  'round',
                            lineJoin: 'round',
                        }}
                    />
                ))}
                {/* Glow pass — slightly wider, more transparent */}
                {runs.map((run, i) => (
                    <Polyline
                        key={`glow-${i}`}
                        positions={run.coords}
                        pathOptions={{
                            color:    run.color,
                            weight:   10,
                            opacity:  0.12,
                            lineCap:  'round',
                            lineJoin: 'round',
                        }}
                    />
                ))}
            </>
        );
    }

    // Fallback — mock routes with no segment data
    const coords = route?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]) || [];
    const color  = PROFILE_COLORS[route.profile] || '#00ff88';
    return (
        <>
            <Polyline positions={coords} pathOptions={{ color, weight: 5, opacity: 0.9, lineCap: 'round' }} />
            <Polyline positions={coords} pathOptions={{ color, weight: 12, opacity: 0.1, lineCap: 'round' }} />
        </>
    );
}

// ── Main MapView ──────────────────────────────────────────────
export default function MapView({
    origin, destination, selectedRoute, routes,
    showAQI, setShowAQI, showBlackspots, setShowBlackspots,
    aqiData, blackspotData, loadingAQI,
    loading, onMapClick, onBoundsChange,
}) {
    const toLL = (r) =>
        r?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]) || [];

    return (
        <div className="map-container">
            <MapContainer
                center={[12.9716, 77.5946]}
                zoom={12}
                style={{ height: '100%', width: '100%' }}
                zoomControl={true}
            >
                {/* Tactical dark map tiles */}
                <TileLayer
                    attribution='&copy; <a href="https://carto.com">CARTO</a>'
                    url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                />

                <MapEvents onMapClick={onMapClick} onBoundsChange={onBoundsChange} />
                {selectedRoute && <FitBounds route={selectedRoute} />}

                {/* Origin marker */}
                {origin.lat && origin.lon && (
                    <Marker position={[+origin.lat, +origin.lon]} icon={originIcon}>
                        <Popup className="tactical-popup">
                            <div style={{
                                fontFamily: 'JetBrains Mono, monospace',
                                fontSize: '11px',
                                color: '#00ff88',
                                background: '#090c14',
                                padding: '6px 8px',
                                borderRadius: '2px',
                            }}>
                                ◎ ORIGIN<br />
                                <span style={{ color: '#6b7a99' }}>
                                    {(+origin.lat).toFixed(5)}, {(+origin.lon).toFixed(5)}
                                </span>
                            </div>
                        </Popup>
                    </Marker>
                )}

                {/* Destination marker */}
                {destination.lat && destination.lon && (
                    <Marker position={[+destination.lat, +destination.lon]} icon={destIcon}>
                        <Popup>
                            <div style={{
                                fontFamily: 'JetBrains Mono, monospace',
                                fontSize: '11px',
                                color: '#ff4560',
                                background: '#090c14',
                                padding: '6px 8px',
                                borderRadius: '2px',
                            }}>
                                ◎ DESTINATION<br />
                                <span style={{ color: '#6b7a99' }}>
                                    {(+destination.lat).toFixed(5)}, {(+destination.lon).toFixed(5)}
                                </span>
                            </div>
                        </Popup>
                    </Marker>
                )}

                {/* Ghost routes — non-selected alternatives */}
                {routes
                    .filter(r => r.route_id !== selectedRoute?.route_id)
                    .map(r => (
                        <Polyline
                            key={r.route_id}
                            positions={toLL(r)}
                            pathOptions={{
                                color:     PROFILE_COLORS[r.profile] || '#6b7a99',
                                weight:    2,
                                opacity:   0.18,
                                dashArray: '6,5',
                            }}
                        />
                    ))}

                {/* Selected route — AQI-coloured segments */}
                {selectedRoute && <SelectedRoute route={selectedRoute} />}

                {/* AQI heatmap circles */}
                {showAQI && aqiData?.features?.map((f, i) => (
                    <CircleMarker
                        key={`aqi-${i}`}
                        center={[f.properties.center_lat, f.properties.center_lon]}
                        radius={5}
                        pathOptions={{
                            color:       'transparent',
                            fillColor:   aqiColor(f.properties.aqi),
                            fillOpacity: 0.28,
                        }}
                    />
                ))}

                {/* Accident blackspots */}
                {showBlackspots && blackspotData?.features?.map((f, i) => {
                    const [lon, lat] = f.geometry.coordinates;
                    const p = f.properties;
                    const r = Math.max(5, Math.min(p.total_accidents / 3, 14));
                    return (
                        <CircleMarker
                            key={`bs-${i}`}
                            center={[lat, lon]}
                            radius={r}
                            pathOptions={{
                                color:       '#ff4560',
                                fillColor:   '#ff4560',
                                fillOpacity: 0.25,
                                weight:      1,
                            }}
                        >
                            <Popup>
                                <div style={{
                                    fontFamily: 'JetBrains Mono, monospace',
                                    fontSize: '11px',
                                    background: '#090c14',
                                    padding: '8px 10px',
                                    borderRadius: '2px',
                                    color: '#d8e0f0',
                                    minWidth: '160px',
                                }}>
                                    <div style={{ color: '#ff4560', marginBottom: 4 }}>
                                        ⚠ BLACKSPOT
                                    </div>
                                    <div style={{ color: '#6b7a99', fontSize: '10px' }}>
                                        SEV: {p.severity?.toUpperCase()}<br />
                                        ACCIDENTS: {p.total_accidents} (FATAL: {p.fatal_accidents})
                                    </div>
                                    {p.description && (
                                        <div style={{ marginTop: 4, fontSize: '10px', color: '#3d4a60' }}>
                                            {p.description}
                                        </div>
                                    )}
                                </div>
                            </Popup>
                        </CircleMarker>
                    );
                })}
            </MapContainer>

            {/* ── Map controls ── */}
            <div className="map-controls">
                <button
                    className={`map-control-btn ${showAQI ? 'active' : ''} ${loadingAQI ? 'loading' : ''}`}
                    onClick={() => setShowAQI(!showAQI)}
                >
                    ◈ AQI Overlay
                </button>
                <button
                    className={`map-control-btn ${showBlackspots ? 'active' : ''}`}
                    onClick={() => setShowBlackspots(!showBlackspots)}
                >
                    ⚠ Blackspots
                </button>
            </div>

            {/* ── AQI legend ── */}
            {showAQI && (
                <div className="aqi-legend">
                    <h4>AQI Scale</h4>
                    <div className="legend-items">
                        {[
                            ['#00ff88', '0–50 Good'],
                            ['#ffb830', '51–100 Moderate'],
                            ['#ff8c00', '101–150 USG'],
                            ['#ff4560', '151–200 Unhealthy'],
                            ['#b06bff', '200+ Hazardous'],
                        ].map(([c, l]) => (
                            <div className="legend-item" key={l}>
                                <div className="legend-swatch" style={{ background: c }} />
                                {l}
                            </div>
                        ))}
                    </div>
                    <p className="legend-note">Route line colour = AQI per segment</p>
                </div>
            )}

            {/* ── Loading overlay ── */}
            {loading && (
                <div className="loading-overlay">
                    <div className="loading-ring" />
                    <p className="loading-text">Computing optimal route…</p>
                </div>
            )}
        </div>
    );
}
