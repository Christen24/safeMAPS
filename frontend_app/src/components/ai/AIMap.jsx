import { useEffect } from 'react';
import { GeoJSON, MapContainer, TileLayer, useMap } from 'react-leaflet';

function FitRoute({ geometries }) {
    const map = useMap();
    useEffect(() => {
        const coords = geometries
            .flatMap(geom => geom?.coordinates || [])
            .filter(point => Array.isArray(point) && point.length >= 2)
            .map(([lon, lat]) => [lat, lon]);
        if (coords.length) {
            map.fitBounds(coords, { padding: [28, 28], maxZoom: 13 });
        }
    }, [geometries, map]);
    return null;
}

function routeStyle(feature) {
    const profile = feature?.properties?.profile;
    const color = profile === 'safest'
        ? '#4ecb8d'
        : profile === 'fastest'
            ? '#4fc3e0'
            : profile === 'healthiest'
                ? '#f0a93e'
                : '#9b87e8';
    return { color, weight: 6, opacity: 0.92 };
}

export default function AIMap({ routes }) {
    const geometries = routes.map(route => route.geometry).filter(Boolean);
    const features = routes
        .filter(route => route.geometry)
        .map(route => ({
            type: 'Feature',
            properties: { profile: route.profile || route.route_id },
            geometry: route.geometry,
        }));

    return (
        <div className="ai-map">
            <MapContainer center={[12.9716, 77.5946]} zoom={11} zoomControl={false}>
                <TileLayer
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    attribution="&copy; OpenStreetMap contributors"
                />
                <FitRoute geometries={geometries} />
                {features.map((feature, index) => (
                    <GeoJSON key={`${feature.properties.profile}-${index}`} data={feature} style={routeStyle} />
                ))}
            </MapContainer>
            {!routes.length && (
                <div className="ai-map-empty">
                    <span>Route geometry will appear here after an MCP route tool completes.</span>
                </div>
            )}
        </div>
    );
}
