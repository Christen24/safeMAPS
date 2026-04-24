import { useEffect, useRef } from 'react';
import {
    MapContainer, TileLayer, Polyline, Marker, Popup,
    CircleMarker, useMapEvents, useMap,
} from 'react-leaflet';
import L from 'leaflet';

delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
    iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

function makeIcon(color) {
    return L.divIcon({
        className: '',
        html: `<div style="
      width:20px;height:20px;background:${color};
      border:3px solid #e2e4f6;border-radius:50%;
      box-shadow:0 0 12px ${color}66, 0 2px 8px rgba(0,0,0,0.5);
    "></div>`,
        iconSize: [20, 20], iconAnchor: [10, 10],
    });
}

const originIcon = makeIcon('#69f6b8');
const destIcon = makeIcon('#ff716c');

const ROUTE_COLORS = {
    balanced: '#c180ff',
    fastest: '#699cff',
    safest: '#69f6b8',
    healthiest: '#f59e0b',
};

export function aqiColor(aqi) {
    if (aqi <= 50) return '#69f6b8';
    if (aqi <= 100) return '#f59e0b';
    if (aqi <= 150) return '#f97316';
    if (aqi <= 200) return '#ff716c';
    if (aqi <= 300) return '#c180ff';
    return '#7f1d1d';
}

export function buildColoredSegments(segments = []) {
    const runs = [];
    let carryPoint = null;

    segments.forEach((segment) => {
        const coords = segment?.geometry?.coordinates;
        if (!Array.isArray(coords) || coords.length === 0) return;

        const color = aqiColor(segment.aqi_value ?? 0);
        const leafletCoords = coords.map(([lon, lat]) => [lat, lon]);
        const currentRun = runs[runs.length - 1];

        if (currentRun?.color === color) {
            const nextCoords = leafletCoords;
            const last = currentRun.coords[currentRun.coords.length - 1];
            const first = nextCoords[0];
            if (last && first && last[0] === first[0] && last[1] === first[1]) {
                currentRun.coords.push(...nextCoords.slice(1));
            } else {
                currentRun.coords.push(...nextCoords);
            }
        } else {
            const runCoords = carryPoint ? [carryPoint, ...leafletCoords] : leafletCoords;
            runs.push({ color, coords: runCoords });
        }

        carryPoint = leafletCoords[leafletCoords.length - 1] || carryPoint;
    });

    return runs.filter((run) => run.coords.length > 1);
}

function MapEvents({ onMapClick, onBoundsChange }) {
    const debounceRef = useRef(null);
    const map = useMapEvents({
        click(e) { onMapClick(e.latlng); },
        moveend() {
            if (debounceRef.current) clearTimeout(debounceRef.current);
            debounceRef.current = setTimeout(() => {
                const b = map.getBounds();
                onBoundsChange({ north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() });
            }, 500);
        },
    });

    useEffect(() => () => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
    }, []);

    useEffect(() => {
        const b = map.getBounds();
        onBoundsChange({ north: b.getNorth(), south: b.getSouth(), east: b.getEast(), west: b.getWest() });
    }, [map, onBoundsChange]);

    return null;
}

function FitBounds({ route }) {
    const map = useMap();
    useEffect(() => {
        if (route?.geometry?.coordinates?.length > 0) {
            const ll = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
            map.fitBounds(L.latLngBounds(ll), { padding: [50, 50] });
        }
    }, [route, map]);
    return null;
}

function SegmentColoredRoute({ route, fallbackColor }) {
    const flatCoords = route?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]) || [];
    const coloredRuns = buildColoredSegments(route?.segments);

    if (coloredRuns.length === 0) {
        return (
            <Polyline
                positions={flatCoords}
                pathOptions={{
                    color: fallbackColor,
                    weight: 5, opacity: 0.9, lineCap: 'round', lineJoin: 'round',
                }}
            />
        );
    }

    return coloredRuns.map((run, index) => (
        <Polyline
            key={`${route.route_id}-aqi-${index}`}
            positions={run.coords}
            pathOptions={{
                color: run.color,
                weight: 5, opacity: 0.95, lineCap: 'round', lineJoin: 'round',
            }}
        />
    ));
}

export default function MapView({
    origin, destination, selectedRoute, routes,
    showAQI, setShowAQI, showBlackspots, setShowBlackspots,
    aqiData, blackspotData, loadingAQI, loading,
    onMapClick, onBoundsChange,
}) {
    const toLL = (route) => route?.geometry?.coordinates?.map(([lon, lat]) => [lat, lon]) || [];
    const aqiFeatures = Array.isArray(aqiData)
        ? aqiData
        : aqiData?.features || [];

    return (
        <div className="map-container">
            <MapContainer center={[12.9716, 77.5946]} zoom={12} style={{ height: '100%', width: '100%' }}>
                <TileLayer
                    attribution='&copy; <a href="https://carto.com">CARTO</a>'
                    url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                />
                <MapEvents onMapClick={onMapClick} onBoundsChange={onBoundsChange} />
                {selectedRoute && <FitBounds route={selectedRoute} />}

                {/* Origin */}
                {origin.lat && origin.lon && (
                    <Marker position={[+origin.lat, +origin.lon]} icon={originIcon}>
                        <Popup><strong style={{ color: '#69f6b8' }}>📍 Origin</strong><br />{(+origin.lat).toFixed(4)}, {(+origin.lon).toFixed(4)}</Popup>
                    </Marker>
                )}

                {/* Destination */}
                {destination.lat && destination.lon && (
                    <Marker position={[+destination.lat, +destination.lon]} icon={destIcon}>
                        <Popup><strong style={{ color: '#ff716c' }}>🏁 Destination</strong><br />{(+destination.lat).toFixed(4)}, {(+destination.lon).toFixed(4)}</Popup>
                    </Marker>
                )}

                {/* Alternative routes (faded, dashed) */}
                {routes.filter(r => r.route_id !== selectedRoute?.route_id).map(r => (
                    <Polyline key={r.route_id} positions={toLL(r)}
                        pathOptions={{ color: ROUTE_COLORS[r.profile] || '#717584', weight: 3, opacity: 0.25, dashArray: '8,6' }} />
                ))}

                {/* Selected route (primary, bold) */}
                {selectedRoute && (
                    <SegmentColoredRoute
                        route={selectedRoute}
                        fallbackColor={ROUTE_COLORS[selectedRoute.profile] || '#c180ff'}
                    />
                )}

                {/* AQI Heatmap */}
                {showAQI && aqiFeatures.map((f, i) => {
                    const props = f.properties || f;
                    return (
                        <CircleMarker key={`aqi-${i}`}
                            center={[props.center_lat, props.center_lon]}
                            radius={5}
                            pathOptions={{ color: 'transparent', fillColor: aqiColor(props.aqi), fillOpacity: 0.3 }} />
                    );
                })}

                {/* Blackspots */}
                {showBlackspots && blackspotData?.features?.map((f, i) => {
                    const [lon, lat] = f.geometry.coordinates;
                    const p = f.properties;
                    return (
                        <CircleMarker key={`bs-${i}`} center={[lat, lon]}
                            radius={Math.max(5, Math.min(p.total_accidents / 3, 14))}
                            pathOptions={{ color: '#ff716c', fillColor: '#ff716c', fillOpacity: 0.35, weight: 1.5 }}>
                            <Popup>
                                <strong style={{ color: '#ff716c' }}>⚠️ Blackspot</strong><br />
                                Severity: {p.severity}<br />Accidents: {p.total_accidents} (Fatal: {p.fatal_accidents})
                                {p.description && <><br /><em>{p.description}</em></>}
                            </Popup>
                        </CircleMarker>
                    );
                })}
            </MapContainer>

            {/* Map Controls */}
            <div className="map-controls">
                <button className={`map-control-btn ${showAQI ? 'active' : ''} ${loadingAQI ? 'loading' : ''}`} onClick={() => setShowAQI(!showAQI)}>
                    🌫️ AQI Heatmap
                </button>
                <button className={`map-control-btn ${showBlackspots ? 'active' : ''}`} onClick={() => setShowBlackspots(!showBlackspots)}>
                    ⚠️ Blackspots
                </button>
            </div>

            {/* AQI Legend */}
            {showAQI && (
                <div className="aqi-legend">
                    <h4>Air Quality Index</h4>
                    <div className="legend-items">
                        {[['#69f6b8', 'Good'], ['#f59e0b', 'Moderate'], ['#f97316', 'USG'], ['#ff716c', 'Unhealthy'], ['#c180ff', 'V.Unhealthy'], ['#7f1d1d', 'Hazardous']].map(([c, l]) => (
                            <div className="legend-item" key={l}>
                                <div className="legend-swatch" style={{ background: c }} />{l}
                            </div>
                        ))}
                    </div>
                    <p className="legend-note">Route colour = AQI along each segment</p>
                </div>
            )}

            {loading && (
                <div className="loading-overlay">
                    <div className="loading-ring" />
                    <p className="loading-text">Computing optimal route...</p>
                </div>
            )}
        </div>
    );
}
