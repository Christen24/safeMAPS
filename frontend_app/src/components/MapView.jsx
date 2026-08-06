import { useEffect, useRef, useCallback, useState, useMemo } from 'react';
import {
    MapContainer, TileLayer, Polyline, Marker,
    Popup, Tooltip, CircleMarker, Rectangle, ScaleControl, useMapEvents, useMap,
} from 'react-leaflet';

import AQIHeatmapLayer from './AQIHeatmapLayer';
import { buildMetroLines, buildMetroStations, METRO_LINE_COLORS } from '../data/nammaMetro';

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
// Extra 10px of canvas below the crosshair holds a soft ground shadow so
// the marker reads as "planted" on the map (same cue Google/Apple pins use
// via their teardrop point) — without giving up the reticle identity.
// The viewBox grew but the crosshair itself is untouched at (11,11), and
// iconAnchor stays [11,11] so the actual lat/lng point doesn't shift.
function makeIcon(ring, fill, { pulse = false } = {}) {
    return L.divIcon({
        className: '',
        html: `
          <svg width="22" height="32" viewBox="0 0 22 32" xmlns="http://www.w3.org/2000/svg">
            <ellipse cx="11" cy="26" rx="5.5" ry="2" fill="#03050a" opacity="0.4"/>
            ${pulse ? `<circle class="sm-origin-pulse" cx="11" cy="11" r="9" fill="none" stroke="${ring}" stroke-width="1.5"/>` : ''}
            <circle cx="11" cy="11" r="9" fill="none" stroke="${ring}" stroke-width="1.5" opacity="0.4"/>
            <circle cx="11" cy="11" r="4"  fill="${fill}" />
            <line x1="11" y1="2"  x2="11" y2="6"  stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="11" y1="16" x2="11" y2="20" stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="2"  y1="11" x2="6"  y2="11" stroke="${ring}" stroke-width="1" opacity="0.5"/>
            <line x1="16" y1="11" x2="20" y2="11" stroke="${ring}" stroke-width="1" opacity="0.5"/>
          </svg>`,
        iconSize: [22, 32],
        iconAnchor: [11, 11],
    });
}

const originIcon = makeIcon('#4ecb8d', '#4ecb8d', { pulse: true });
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

// ── Blackspot severity colors — matches index.css Monsoon Ledger vars ──
// (--acid / --amber / --infra / --infra-dim). Hardcoded because Leaflet
// pathOptions are SVG presentation attributes, not CSS, so var() won't
// resolve here — same convention the destIcon color above already uses.
const SEVERITY_COLORS = {
    low:      '#4ecb8d', // --acid
    moderate: '#f0a93e', // --amber
    high:     '#f16565', // --infra
    critical: '#c94545', // --infra-dim
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
          <div style="position:relative; width:34px; height:34px;">
            <svg width="34" height="34" viewBox="0 0 34 34" xmlns="http://www.w3.org/2000/svg" style="position:absolute; inset:0;">
              <circle class="sm-live-pulse-ring" cx="17" cy="17" r="10" fill="none" stroke="#4fc3e0" stroke-width="1.5"/>
              <circle cx="17" cy="17" r="15" fill="#4fc3e0" fill-opacity="0.15"/>
            </svg>
            <svg width="34" height="34" viewBox="0 0 34 34" xmlns="http://www.w3.org/2000/svg"
                 style="position:absolute; inset:0; transform: rotate(${heading}deg); transform-origin: 50% 50%;">
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

// ── Zoom-aware line weight — routes thicken on zoom-in ─────────
// Mirrors Google/Apple Maps: the route reads as a thin ribbon at city
// zoom and a bold highway-shield stroke once you're in close.
function useZoomLevel() {
    const map = useMap();
    const [zoom, setZoom] = useState(() => map.getZoom());
    useMapEvents({
        zoom:    () => setZoom(map.getZoom()),
        zoomend: () => setZoom(map.getZoom()),
    });
    return zoom;
}

function routeWeights(zoom) {
    if (zoom >= 16) return { shadow: 12.5, casing: 10.5, fill: 6.5, flow: 3   };
    if (zoom >= 14) return { shadow: 10.5, casing: 9,    fill: 5.5, flow: 2.6 };
    if (zoom >= 12) return { shadow: 9,    casing: 7.5,  fill: 4.5, flow: 2.2 };
    return              { shadow: 7.5,  casing: 6.5,  fill: 4,   flow: 2   };
}

// ── Selected route — the "cut-line" treatment ──────────────────
// Four stacked passes instead of one flat stroke, closest to how
// Google/Apple/Uber actually draw a route over satellite imagery:
//   1. shadow  — soft dark halo, reads as elevation off the basemap
//   2. casing  — off-white seam, the contrast edge that cuts through
//                photographic satellite tiles (this is what a flat
//                colour line was missing)
//   3. fill    — the real signal: AQI / traffic / profile colour,
//                per coloured run
//   4. flow    — animated ticks of light drifting origin → destination
// Shadow/casing/flow always use the FULL route geometry (present even
// on mock/offline routes with no `segments`), so demo mode gets the
// same treatment as a live-backend route — that's the case that was
// showing the flat green line.
function SelectedRoute({ route, colorMode }) {
    const zoom = useZoomLevel();
    const w = routeWeights(zoom);
    const hasSegments = route?.segments?.length > 0;

    const fullCoords = route?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]) || [];
    if (fullCoords.length < 2) return null;

    const runs = hasSegments
        ? (colorMode === 'traffic' ? buildTrafficColoredSegments(route.segments) : buildColoredSegments(route.segments))
        : [{ color: PROFILE_COLORS[route.profile] || '#4ecb8d', coords: fullCoords }];

    return (
        <>
            <Polyline
                positions={fullCoords}
                pathOptions={{
                    className: 'sm-route-shadow',
                    color: '#03050a', weight: w.shadow, opacity: 0.38,
                    lineCap: 'round', lineJoin: 'round', interactive: false,
                }}
            />
            <Polyline
                positions={fullCoords}
                pathOptions={{
                    color: '#eef1fb', weight: w.casing, opacity: 0.92,
                    lineCap: 'round', lineJoin: 'round', interactive: false,
                }}
            />
            {runs.map((run, i) => (
                <Polyline
                    key={`fill-${i}`}
                    positions={run.coords}
                    pathOptions={{
                        color: run.color, weight: w.fill, opacity: 0.98,
                        lineCap: 'round', lineJoin: 'round',
                    }}
                />
            ))}
            <Polyline
                positions={fullCoords}
                pathOptions={{
                    className: 'sm-route-flow',
                    color: '#ffffff', weight: w.flow, opacity: 0.75,
                    dashArray: '2 16', lineCap: 'round', interactive: false,
                }}
            />
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
    onStartJourney, onStopJourney, onRecenter, onSelectRoute,
}) {
    const [colorMode, setColorMode] = useState('traffic'); // 'traffic' | 'aqi'
    const [hoveredGhostId, setHoveredGhostId] = useState(null);
    const [showMetro, setShowMetro] = useState(true);
    const metroLines = useMemo(() => buildMetroLines(), []);
    const metroStations = useMemo(() => buildMetroStations(), []);
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

                {/* Metric scale bar — India uses metric throughout the rest of the UI */}
                <ScaleControl position="bottomleft" imperial={false} maxWidth={120} />

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

                {/* Namma Metro — real BMRCL line colours, current operational
                    network (3 lines, 85 stations, 2 interchanges). Rendered as
                    background context (thin, ~0.8 opacity, no casing/shadow)
                    and — importantly — before every marker/route below, so
                    it's the bottom-most vector layer and never paints over
                    the origin/destination pins or the routed path. */}
                {showMetro && metroLines.map(({ line, color, stations }) => (
                    <Polyline
                        key={line}
                        positions={stations.map(s => [s.lat, s.lon])}
                        pathOptions={{
                            color,
                            weight: 3,
                            opacity: 0.82,
                            lineCap: 'round',
                            lineJoin: 'round',
                        }}
                    />
                ))}
                {showMetro && metroStations.map(s => (
                    <CircleMarker
                        key={s.code}
                        center={[s.lat, s.lon]}
                        radius={s.interchange ? 5 : 3}
                        pathOptions={{
                            color: '#0b0f1a',
                            weight: s.interchange ? 2 : 1.5,
                            fillColor: s.interchange ? '#eef1fb' : METRO_LINE_COLORS[s.lines[0]],
                            fillOpacity: 1,
                        }}
                    >
                        <Tooltip direction="top" offset={[0, -4]} opacity={1}>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                                {s.name}
                                {s.interchange && (
                                    <><br /><span style={{ opacity: 0.65 }}>Interchange — {s.lines.join(' / ')}</span></>
                                )}
                            </span>
                        </Tooltip>
                    </CircleMarker>
                ))}

                {/* Origin marker */}
                {origin.lat && origin.lon && (
                    <Marker position={[+origin.lat, +origin.lon]} icon={originIcon}>
                        <Popup>
                            <div className="sm-popup">
                                <div className="sm-popup-head" style={{ color: '#4ecb8d' }}>
                                    <span className="sm-popup-dot" style={{ background: '#4ecb8d', boxShadow: '0 0 6px #4ecb8d99' }} />
                                    ORIGIN
                                </div>
                                <div className="sm-popup-coords">
                                    {(+origin.lat).toFixed(5)}, {(+origin.lon).toFixed(5)}
                                </div>
                            </div>
                        </Popup>
                    </Marker>
                )}

                {/* Destination marker */}
                {destination.lat && destination.lon && (
                    <Marker position={[+destination.lat, +destination.lon]} icon={destIcon}>
                        <Popup>
                            <div className="sm-popup">
                                <div className="sm-popup-head" style={{ color: '#f16565' }}>
                                    <span className="sm-popup-dot" style={{ background: '#f16565', boxShadow: '0 0 6px #f1656599' }} />
                                    DESTINATION
                                </div>
                                <div className="sm-popup-coords">
                                    {(+destination.lat).toFixed(5)}, {(+destination.lon).toFixed(5)}
                                </div>
                            </div>
                        </Popup>
                    </Marker>
                )}

                {/* Ghost routes — non-selected alternatives. Neutral grey-blue
                    (not the profile colour) so the coloured selected route is
                    unambiguous at a glance — same convention Google Maps
                    uses for alternates. Hover/click to swap the primary
                    route, mirroring tap-to-switch behaviour there too. */}
                {routes
                    .filter(r => r.route_id !== selectedRoute?.route_id)
                    .map(r => {
                        const isHovered = hoveredGhostId === r.route_id;
                        return (
                            <Polyline
                                key={r.route_id}
                                positions={toLL(r)}
                                pathOptions={{
                                    color: isHovered ? '#aab4d9' : '#727ea8',
                                    weight: isHovered ? 4.5 : 3,
                                    opacity: isHovered ? 0.85 : 0.45,
                                    dashArray: '1 7',
                                    lineCap: 'round',
                                }}
                                eventHandlers={{
                                    mouseover: () => setHoveredGhostId(r.route_id),
                                    mouseout:  () => setHoveredGhostId(id => (id === r.route_id ? null : id)),
                                    click:     () => onSelectRoute?.(r),
                                }}
                            />
                        );
                    })}

                {/* Selected route — coloured by AQI or traffic */}
                {selectedRoute && <SelectedRoute route={selectedRoute} colorMode={colorMode} />}

                {/* AQI heatmap — zoom-aware radius, stable keys, satellite contrast */}
                {showAQI && <AQIHeatmapLayer aqiData={aqiData} />}

                {/* Accident blackspots */}
                {showBlackspots && blackspotData?.features?.map((f, i) => {
                    const [lon, lat] = f.geometry.coordinates;
                    const p = f.properties;
                    // severity_weight is a normalized 0-10 scale regardless of data
                    // source, so it stays meaningful whether a blackspot came from
                    // the small built-in list (total_accidents ~10-45) or real BTP
                    // station aggregates (total_accidents ~19-1787). Falling back to
                    // the old total_accidents heuristic only if a record predates
                    // the severity_weight column.
                    const weight = p.severity_weight != null
                        ? p.severity_weight
                        : Math.min(p.total_accidents / 4.5, 10);
                    const r = 5 + (weight / 10) * 11; // 5px (low) .. 16px (critical)
                    const sevColor = SEVERITY_COLORS[p.severity] || SEVERITY_COLORS.moderate;
                    return (
                        <CircleMarker
                            key={`bs-${i}`}
                            center={[lat, lon]}
                            radius={r}
                            pathOptions={{
                                color: sevColor,
                                fillColor: sevColor,
                                fillOpacity: 0.28,
                                weight: 1,
                            }}
                        >
                            <Popup>
                                <div className="sm-popup" style={{ minWidth: '170px' }}>
                                    <div className="sm-popup-head" style={{ color: '#f16565' }}>
                                        <span className="sm-popup-dot" style={{ background: '#f16565', boxShadow: '0 0 6px #f1656599' }} />
                                        BLACKSPOT
                                    </div>
                                    <div className="sm-popup-divider" />
                                    <div className="sm-popup-body">
                                        SEV: {p.severity?.toUpperCase()}
                                        {p.severity_weight != null && ` (${p.severity_weight.toFixed(1)}/10)`}<br />
                                        ACCIDENTS: {p.total_accidents} <span className="sm-popup-dim">(FATAL: {p.fatal_accidents})</span>
                                    </div>
                                    {p.description && (
                                        <div className="sm-popup-desc">{p.description}</div>
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
                                <div className="sm-popup" style={{ minWidth: '190px' }}>
                                    <div className="sm-popup-head" style={{ color }}>
                                        <span className="sm-popup-dot" style={{ background: color, boxShadow: `0 0 6px ${color}99` }} />
                                        {p.incident_type?.toUpperCase()}
                                    </div>
                                    <div className="sm-popup-divider" />
                                    <div className="sm-popup-body">
                                        SRC: {p.source?.toUpperCase()} &nbsp; SEV: {p.severity}/3
                                    </div>
                                    {p.description && (
                                        <div className="sm-popup-desc">{p.description.slice(0, 120)}</div>
                                    )}
                                    <div className="sm-popup-meta">
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
                    className={`map-control-btn ${showMetro ? 'active' : ''}`}
                    onClick={() => setShowMetro(!showMetro)}
                >
                    🚇 Metro Network
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
                {/* Route line colour-mode toggle — only when the selected route
                    actually has per-segment data. Bug fix: this used to show
                    for mock/offline routes too, but those have no segments to
                    recolour, so tapping it silently did nothing. */}
                {selectedRoute?.segments?.length > 0 && (
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
            {(showAQI || (selectedRoute?.segments?.length > 0 && colorMode === 'traffic')) && (
                <div className="aqi-legend">
                    {colorMode === 'traffic' && selectedRoute?.segments?.length > 0 ? (
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
