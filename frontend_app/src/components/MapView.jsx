import { useEffect, useRef, useCallback, useState } from 'react';
import {
    MapContainer, TileLayer, Polyline, Marker,
    Popup, CircleMarker, Rectangle, useMapEvents, useMap,
} from 'react-leaflet';

import AQIHeatmapLayer from './AQIHeatmapLayer';

// (AQILayer + useMapZoom removed — replaced by canvas-based AQIHeatmapLayer)
import L from 'leaflet';

// ── Fix default Leaflet icon paths ────────────────────────────
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
    iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

// Loaded OSM road-network coverage — must match backend/config.py
// (bbox_min_lat/max_lat/min_lon/max_lon). Shown as a boundary overlay so
// clicks near the edge (which fail with "no road found within 500m")
// are visually explained rather than a mystery.
const NETWORK_BOUNDS = [
    [12.75, 77.35],
    [13.25, 77.90],
];

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
        iconSize: [22, 22],
        iconAnchor: [11, 11],
    });
}

const originIcon = makeIcon('#4ecb8d', '#4ecb8d');
const destIcon = makeIcon('#f16565', '#f16565');

// ── Incident triangle icons ───────────────────────────────────
// accident=orange-red, closure=red, waterlogging=blue, construction=amber, hazard=yellow
const INCIDENT_COLORS = {
    accident: '#f97316',
    closure: '#ef4444',
    waterlogging: '#3b82f6',
    construction: '#f59e0b',
    hazard: '#eab308',
};

function makeTriangleIcon(color) {
    return L.divIcon({
        className: '',
        html: `
          <svg width="20" height="18" viewBox="0 0 20 18" xmlns="http://www.w3.org/2000/svg">
            <polygon points="10,1 19,17 1,17" fill="${color}" fill-opacity="0.85"
                     stroke="#0d1117" stroke-width="1.2"/>
            <text x="10" y="14" text-anchor="middle" font-size="8" font-family="monospace"
                  fill="#0d1117" font-weight="bold">⚠</text>
          </svg>`,
        iconSize: [20, 18],
        iconAnchor: [10, 18],
    });
}

// ── Live-position navigation arrow — rotates to heading ────────
function makeArrowIcon(heading) {
    return L.divIcon({
        className: '',
        html: `
          <div style="transform: rotate(${heading}deg); transform-origin: 50% 50%;">
            <svg width="34" height="34" viewBox="0 0 34 34" xmlns="http://www.w3.org/2000/svg">
              <circle cx="17" cy="17" r="15" fill="#4fc3e0" fill-opacity="0.15"/>
              <polygon points="17,4 25,26 17,21 9,26" fill="#4fc3e0"
                       stroke="#0d1322" stroke-width="1.5"/>
            </svg>
          </div>`,
        iconSize:   [34, 34],
        iconAnchor: [17, 17],
    });
}

// ── Turn-instruction display helpers ────────────────────────────
const MANEUVER_ICONS = {
    depart: '↑',
    straight: '↑',
    slight_left: '↖',
    slight_right: '↗',
    left: '←',
    right: '→',
    uturn: '↩',
    arrive: '⚑',
};

function formatDistance(m) {
    if (m < 1000) return `${Math.round(m / 10) * 10} m`;
    return `${(m / 1000).toFixed(1)} km`;
}

// ── Profile colours ───────────────────────────────────────────
const PROFILE_COLORS = {
    balanced: '#9b87e8',
    fastest: '#4fc3e0',
    safest: '#4ecb8d',
    healthiest: '#f0a93e',
};

// ── AQI colour scale ──────────────────────────────────────────
export function aqiColor(aqi) {
    if (aqi <= 50) return '#4ecb8d';   // Good — acid green
    if (aqi <= 100) return '#f0a93e';   // Moderate — amber
    if (aqi <= 150) return '#ff8c00';   // Unhealthy sensitive — dark orange
    if (aqi <= 200) return '#f16565';   // Unhealthy — infrared
    if (aqi <= 300) return '#9b87e8';   // Very unhealthy — violet
    return '#7b1fa2';                   // Hazardous — deep purple
}

// ── Traffic congestion colour scale ───────────────────────────
// congestion: 0.0 = free-flowing (green), 1.0 = gridlock (red)
export function trafficColor(congestion) {
    if (congestion <= 0.15) return '#4ecb8d';  // Free-flowing — green
    if (congestion <= 0.35) return '#a8d96a';  // Light — yellow-green
    if (congestion <= 0.55) return '#f0a93e';  // Moderate — amber
    if (congestion <= 0.75) return '#f97316';  // Heavy — orange
    return '#f16565';                          // Severe — red
}

// ── Generic segment colour-run builder ────────────────────────
function buildColorRuns(segments, colorFn) {
    if (!segments || segments.length === 0) return [];

    const runs = [];
    let currentColor = null;
    let currentCoords = [];

    for (const seg of segments) {
        const coords = seg.geometry?.coordinates;
        if (!coords || coords.length === 0) continue;

        const color = colorFn(seg);

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

// AQI-coloured run builder (existing behaviour)
function buildColoredSegments(segments) {
    return buildColorRuns(segments, seg => aqiColor(seg.aqi_value));
}

// Traffic-coloured run builder
function buildTrafficColoredSegments(segments) {
    return buildColorRuns(segments, seg => trafficColor(seg.congestion ?? 0));
}


// ── Debounced map events ──────────────────────────────────────
function MapEvents({ onMapClick, onBoundsChange }) {
    const debounceRef = useRef(null);
    const emitBounds = useCallback((mapInstance) => {
        const b = mapInstance.getBounds();
        onBoundsChange({
            north: b.getNorth(), south: b.getSouth(),
            east: b.getEast(), west: b.getWest(),
        });
    }, [onBoundsChange]);

    const map = useMapEvents({
        click(e) { onMapClick(e.latlng); },
        moveend() {
            if (debounceRef.current) clearTimeout(debounceRef.current);
            debounceRef.current = setTimeout(() => emitBounds(map), 500);
        },
    });

    useEffect(() => {
        emitBounds(map);
    }, [emitBounds, map]);

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

// ── Live position: arrow marker + auto-follow / recenter ───────
// - While `navigating`, the map pans to keep the arrow centered on every
//   position update ("follow me" behaviour).
// - `recenterTick` is a counter bumped by the "My Location" button —
//   incrementing it (even with no other state change) re-triggers the
//   pan, which is why it's in the effect's dependency array despite not
//   being read inside it.
function LiveLocationMarker({ position, navigating, recenterTick }) {
    const map = useMap();

    useEffect(() => {
        if (!position) return;
        if (navigating) {
            map.panTo([position.lat, position.lon], { animate: true, duration: 0.4 });
        }
    }, [position, navigating, map]);

    useEffect(() => {
        if (recenterTick > 0 && position) {
            map.setView([position.lat, position.lon], Math.max(map.getZoom(), 16), { animate: true });
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [recenterTick]);

    if (!position) return null;

    return (
        <Marker
            position={[position.lat, position.lon]}
            icon={makeArrowIcon(position.heading || 0)}
            zIndexOffset={1000}
        />
    );
}

// ── Selected route — segment-coloured or flat ─────────────────
function SelectedRoute({ route, colorMode }) {
    const hasSegments = route?.segments?.length > 0;

    if (hasSegments) {
        const runs = colorMode === 'traffic'
            ? buildTrafficColoredSegments(route.segments)
            : buildColoredSegments(route.segments);
        return (
            <>
                {runs.map((run, i) => (
                    <Polyline
                        key={`run-${i}`}
                        positions={run.coords}
                        pathOptions={{
                            color: run.color,
                            weight: 5,
                            opacity: 0.9,
                            lineCap: 'round',
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
                            color: run.color,
                            weight: 10,
                            opacity: 0.12,
                            lineCap: 'round',
                            lineJoin: 'round',
                        }}
                    />
                ))}
            </>
        );
    }

    // Fallback — mock routes with no segment data
    const coords = route?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]) || [];
    const color = PROFILE_COLORS[route.profile] || '#4ecb8d';
    return (
        <>
            <Polyline positions={coords} pathOptions={{ color, weight: 5, opacity: 0.9, lineCap: 'round' }} />
            <Polyline positions={coords} pathOptions={{ color, weight: 12, opacity: 0.1, lineCap: 'round' }} />
        </>
    );
}

// ── Main MapView ────────────────────────────────────────
// Bug 3 fix: incidentData is now passed as a prop from App.jsx
// (which already fetches incidents every 10 min).
// This removes the duplicate fetch that was previously here,
// which caused 2 API calls every 10 min and could show
// different counts on the NavBar vs map markers.
export default function MapView({
    origin, destination, selectedRoute, routes,
    showAQI, setShowAQI, showBlackspots, setShowBlackspots,
    showIncidents, setShowIncidents,
    aqiData, blackspotData, loadingAQI,
    incidentData,           // Bug 3 fix: received from App.jsx, not fetched here
    loadingIncidents,
    loading, onMapClick, onBoundsChange,
    pickingDestOnMap,       // when true: crosshair cursor + overlay hint
    navigating, livePosition, currentStep, navError, recenterTick,
    onStartJourney, onStopJourney, onRecenter,
}) {
    const [colorMode, setColorMode] = useState('traffic'); // 'traffic' | 'aqi'
    const toLL = (r) =>
        r?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]) || [];

    return (
        <div className={`map-container${pickingDestOnMap ? ' picking-dest' : ''}`}>
            {/* Overlay hint when in pick-destination mode */}
            {pickingDestOnMap && (
                <div className="pick-dest-overlay">
                    <span className="pick-dest-overlay-icon">🗺</span>
                    Click anywhere on the map to set your destination
                </div>
            )}
            <MapContainer
                center={[12.9716, 77.5946]}
                zoom={12}
                style={{ height: '100%', width: '100%' }}
                zoomControl={true}
            >
                {/* Esri satellite imagery */}
                <TileLayer
                    attribution='Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
                    url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
                    maxZoom={19}
                />
                {/* Hybrid labels overlay — street names, place names on top of satellite */}
                <TileLayer
                    url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
                    maxZoom={19}
                    opacity={0.85}
                />

                <MapEvents onMapClick={onMapClick} onBoundsChange={onBoundsChange} />
                {selectedRoute && <FitBounds route={selectedRoute} />}
                <LiveLocationMarker
                    position={livePosition}
                    navigating={navigating}
                    recenterTick={recenterTick}
                />

                {/* Loaded road-network coverage boundary — clicks outside
                    this (or within ~500m of its edge) won't find a road. */}
                <Rectangle
                    bounds={NETWORK_BOUNDS}
                    pathOptions={{
                        color: '#838cb0',
                        weight: 1,
                        dashArray: '6 6',
                        fill: false,
                        interactive: false,
                    }}
                />

                {/* Origin marker */}
                {origin.lat && origin.lon && (
                    <Marker position={[+origin.lat, +origin.lon]} icon={originIcon}>
                        <Popup className="tactical-popup">
                            <div style={{
                                fontFamily: 'JetBrains Mono, monospace',
                                fontSize: '11px',
                                color: '#4ecb8d',
                                background: '#090c14',
                                padding: '6px 8px',
                                borderRadius: '2px',
                            }}>
                                ◎ ORIGIN<br />
                                <span style={{ color: '#b3bbd6' }}>
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
                                color: '#f16565',
                                background: '#090c14',
                                padding: '6px 8px',
                                borderRadius: '2px',
                            }}>
                                ◎ DESTINATION<br />
                                <span style={{ color: '#b3bbd6' }}>
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
                                color: PROFILE_COLORS[r.profile] || '#b3bbd6',
                                weight: 2,
                                opacity: 0.18,
                                dashArray: '6,5',
                            }}
                        />
                    ))}

                {/* Selected route — coloured by AQI or traffic */}
                {selectedRoute && <SelectedRoute route={selectedRoute} colorMode={colorMode} />}

                {/* AQI heatmap — zoom-aware radius, stable keys, satellite contrast */}
                {showAQI && <AQIHeatmapLayer aqiData={aqiData} />}

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
                                color: '#f16565',
                                fillColor: '#f16565',
                                fillOpacity: 0.25,
                                weight: 1,
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
                                    <div style={{ color: '#f16565', marginBottom: 4 }}>
                                        ⚠ BLACKSPOT
                                    </div>
                                    <div style={{ color: '#b3bbd6', fontSize: '10px' }}>
                                        SEV: {p.severity?.toUpperCase()}<br />
                                        ACCIDENTS: {p.total_accidents} (FATAL: {p.fatal_accidents})
                                    </div>
                                    {p.description && (
                                        <div style={{ marginTop: 4, fontSize: '10px', color: '#838cb0' }}>
                                            {p.description}
                                        </div>
                                    )}
                                </div>
                            </Popup>
                        </CircleMarker>
                    );
                })}

                {/* Live incidents layer */}
                {showIncidents && incidentData?.features?.map((f, i) => {
                    const [lon, lat] = f.geometry.coordinates;
                    const p = f.properties;
                    const color = INCIDENT_COLORS[p.incident_type] || '#eab308';
                    return (
                        <Marker
                            key={`inc-${i}`}
                            position={[lat, lon]}
                            icon={makeTriangleIcon(color)}
                        >
                            <Popup>
                                <div style={{
                                    fontFamily: 'JetBrains Mono, monospace',
                                    fontSize: '11px',
                                    background: '#090c14',
                                    padding: '8px 10px',
                                    borderRadius: '2px',
                                    color: '#d8e0f0',
                                    minWidth: '180px',
                                }}>
                                    <div style={{ color, marginBottom: 4, fontWeight: 700 }}>
                                        ⚠ {p.incident_type?.toUpperCase()}
                                    </div>
                                    <div style={{ color: '#b3bbd6', fontSize: '10px' }}>
                                        SRC: {p.source?.toUpperCase()} &nbsp; SEV: {p.severity}/3
                                    </div>
                                    {p.description && (
                                        <div style={{ marginTop: 4, fontSize: '10px', color: '#8892aa' }}>
                                            {p.description.slice(0, 120)}
                                        </div>
                                    )}
                                    <div style={{ marginTop: 4, fontSize: '9px', color: '#838cb0' }}>
                                        EXPIRES: {new Date(p.expires_at).toLocaleTimeString()}
                                    </div>
                                </div>
                            </Popup>
                        </Marker>
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
                <button
                    className={`map-control-btn incident-btn ${showIncidents ? 'active' : ''} ${loadingIncidents ? 'loading' : ''}`}
                    onClick={() => setShowIncidents(!showIncidents)}
                >
                    ▲ Live Incidents
                    {incidentData?.total > 0 && (
                        <span className="incident-badge">{incidentData.total}</span>
                    )}
                </button>
                {/* Route line colour-mode toggle — only show when a route is selected */}
                {selectedRoute && (
                    <button
                        className={`map-control-btn color-mode-btn ${colorMode === 'traffic' ? 'active' : ''}`}
                        onClick={() => setColorMode(m => m === 'aqi' ? 'traffic' : 'aqi')}
                        title={colorMode === 'aqi' ? 'Switch to traffic density colouring' : 'Switch to AQI colouring'}
                    >
                        {colorMode === 'aqi' ? '🟡 Show Traffic' : '🌫️ Show AQI'}
                    </button>
                )}
            </div>

            {/* ── My Location (Google-Maps-style recenter) ── */}
            <button
                className="recenter-btn"
                onClick={onRecenter}
                title="Center on my location"
                aria-label="Center on my location"
            >
                <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <circle cx="10" cy="10" r="3" fill="#4fc3e0" />
                    <circle cx="10" cy="10" r="7" stroke="#4fc3e0" strokeWidth="1.5" fill="none" />
                    <line x1="10" y1="0"  x2="10" y2="4"  stroke="#4fc3e0" strokeWidth="1.5" />
                    <line x1="10" y1="16" x2="10" y2="20" stroke="#4fc3e0" strokeWidth="1.5" />
                    <line x1="0"  y1="10" x2="4"  y2="10" stroke="#4fc3e0" strokeWidth="1.5" />
                    <line x1="16" y1="10" x2="20" y2="10" stroke="#4fc3e0" strokeWidth="1.5" />
                </svg>
            </button>

            {/* ── Journey controls ── */}
            {!navigating && selectedRoute && (
                <button className="start-journey-btn" onClick={onStartJourney}>
                    ▶ Start Journey
                </button>
            )}

            {navigating && (
                <div className="nav-instruction-panel">
                    {navError ? (
                        <p className="nav-error-text">⚠ {navError}</p>
                    ) : selectedRoute?.instructions?.[currentStep] ? (
                        <>
                            <div className="nav-instruction-main">
                                <span className="nav-maneuver-icon">
                                    {MANEUVER_ICONS[selectedRoute.instructions[currentStep].maneuver] || '↑'}
                                </span>
                                <div>
                                    <p className="nav-instruction-text">
                                        {selectedRoute.instructions[currentStep].instruction}
                                    </p>
                                    {selectedRoute.instructions[currentStep].distance_m > 0 && (
                                        <p className="nav-instruction-distance">
                                            {formatDistance(selectedRoute.instructions[currentStep].distance_m)}
                                        </p>
                                    )}
                                </div>
                            </div>
                        </>
                    ) : (
                        <p className="nav-instruction-text">Tracking your location…</p>
                    )}
                    <button className="end-journey-btn" onClick={onStopJourney}>✕ End</button>
                </div>
            )}

            {/* ── Legend — updates based on active colour mode ── */}
            {(showAQI || (selectedRoute && colorMode === 'traffic')) && (
                <div className="aqi-legend">
                    {colorMode === 'traffic' && selectedRoute ? (
                        <>
                            <h4>Traffic Density</h4>
                            <div className="legend-items">
                                {[
                                    ['#4ecb8d', 'Free-flowing'],
                                    ['#a8d96a', 'Light'],
                                    ['#f0a93e', 'Moderate'],
                                    ['#f97316', 'Heavy'],
                                    ['#f16565', 'Severe'],
                                ].map(([c, l]) => (
                                    <div className="legend-item" key={l}>
                                        <div className="legend-swatch" style={{ background: c }} />
                                        {l}
                                    </div>
                                ))}
                            </div>
                            <p className="legend-note">Route line colour = live traffic speed</p>
                        </>
                    ) : (
                        <>
                            <h4>AQI Scale</h4>
                            <div className="legend-items">
                                {[
                                    ['#4ecb8d', '0–50 Good'],
                                    ['#f0a93e', '51–100 Moderate'],
                                    ['#ff8c00', '101–150 USG'],
                                    ['#f16565', '151–200 Unhealthy'],
                                    ['#9b87e8', '200+ Hazardous'],
                                ].map(([c, l]) => (
                                    <div className="legend-item" key={l}>
                                        <div className="legend-swatch" style={{ background: c }} />
                                        {l}
                                    </div>
                                ))}
                            </div>
                            <p className="legend-note">Route line colour = AQI per segment</p>
                        </>
                    )}
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
