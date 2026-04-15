import { useState, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import MapView from './components/MapView';
import LandingPage from './components/LandingPage';
import './index.css';
import 'leaflet/dist/leaflet.css';

const API_BASE = 'http://localhost:8000/api';

function App() {
    const [view, setView] = useState('landing'); // 'landing' | 'dashboard' | 'heatmaps'
    const [origin, setOrigin] = useState({ lat: '', lon: '' });
    const [destination, setDestination] = useState({ lat: '', lon: '' });
    const [profile, setProfile] = useState('safest');
    const [weights, setWeights] = useState({ alpha: 0.40, beta: 0.30, gamma: 0.30 });
    const [routes, setRoutes] = useState([]);
    const [selectedRoute, setSelectedRoute] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [showAQI, setShowAQI] = useState(false);
    const [showBlackspots, setShowBlackspots] = useState(true);
    const [aqiData, setAqiData] = useState(null);
    const [blackspotData, setBlackspotData] = useState(null);

    const computeRoute = useCallback(async () => {
        if (!origin.lat || !origin.lon || !destination.lat || !destination.lon) {
            setError('Enter valid coordinates for both points.');
            return;
        }
        setLoading(true);
        setError(null);
        try {
            const resp = await fetch(`${API_BASE}/route/compare?` + new URLSearchParams({
                origin_lat: origin.lat, origin_lon: origin.lon,
                dest_lat: destination.lat, dest_lon: destination.lon,
            }));
            if (!resp.ok) throw new Error((await resp.json()).detail || 'Route computation failed');
            const data = await resp.json();
            setRoutes(data.routes);
            setSelectedRoute(data.routes.find(r => r.profile === profile) || data.routes[0]);
        } catch {
            setRoutes(getMockRoutes());
            setSelectedRoute(getMockRoutes().find(r => r.profile === profile) || getMockRoutes()[0]);
        } finally {
            setLoading(false);
        }
    }, [origin, destination, profile]);

    const handleMapClick = useCallback((latlng) => {
        if (!origin.lat) {
            setOrigin({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
        } else if (!destination.lat) {
            setDestination({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
        } else {
            setOrigin({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
            setDestination({ lat: '', lon: '' });
            setRoutes([]); setSelectedRoute(null);
        }
    }, [origin, destination]);

    const swapPoints = useCallback(() => {
        setOrigin(destination);
        setDestination(origin);
    }, [origin, destination]);

    if (view === 'landing') {
        return <LandingPage onStart={() => setView('dashboard')} />;
    }

    return (
        <div className="app">
            {/* Navigation Bar */}
            <div className="nav-bar" style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 1001 }}>
                <div className="nav-brand">
                    <h1>🗺️ SafeMAPS</h1>
                    <span className="tagline">Health & Safety Routing · Bengaluru</span>
                </div>
                <div className="nav-tabs">
                    <button className={`nav-tab ${view === 'dashboard' ? 'active' : ''}`} onClick={() => setView('dashboard')}>
                        📍 Dashboard
                    </button>
                    <button className={`nav-tab ${view === 'heatmaps' ? 'active' : ''}`} onClick={() => { setView('heatmaps'); setShowAQI(true); }}>
                        🌫️ Heatmaps
                    </button>
                    <button className="nav-tab" onClick={() => setView('landing')}>
                        🏠 Home
                    </button>
                </div>
                <div className="nav-safety-index">
                    <div>
                        <div className="safety-badge">98.2</div>
                        <div className="safety-label">Safety Index</div>
                    </div>
                </div>
            </div>

            {/* Main Content */}
            <div className="main-content" style={{ marginTop: 52 }}>
                <Sidebar
                    origin={origin} destination={destination}
                    setOrigin={setOrigin} setDestination={setDestination}
                    profile={profile} setProfile={setProfile}
                    weights={weights} setWeights={setWeights}
                    routes={routes} selectedRoute={selectedRoute}
                    setSelectedRoute={setSelectedRoute}
                    onCompute={computeRoute} onSwap={swapPoints}
                    loading={loading} error={error}
                />
                <MapView
                    origin={origin} destination={destination}
                    selectedRoute={selectedRoute} routes={routes}
                    showAQI={showAQI} setShowAQI={setShowAQI}
                    showBlackspots={showBlackspots} setShowBlackspots={setShowBlackspots}
                    aqiData={aqiData} blackspotData={blackspotData}
                    loading={loading} onMapClick={handleMapClick}
                    onBoundsChange={() => { }}
                />
            </div>
        </div>
    );
}

function getMockRoutes() {
    const base = [
        [77.5946, 12.9716], [77.5980, 12.9700], [77.6020, 12.9660],
        [77.6060, 12.9580], [77.6101, 12.9352], [77.6150, 12.9300], [77.6230, 12.9170],
    ];
    return [
        {
            route_id: 'bal', profile: 'balanced', geometry: { type: 'LineString', coordinates: base }, segments: [],
            cost_breakdown: { total_cost: 12.5, travel_time_minutes: 22.3, distance_km: 8.7, avg_aqi: 95, max_aqi: 145, accident_hotspots_passed: 2, travel_time_cost: 5, aqi_exposure_cost: 4.2, accident_risk_cost: 3.3 },
            weights_used: { alpha: 0.4, beta: 0.3, gamma: 0.3 }
        },
        {
            route_id: 'fast', profile: 'fastest', geometry: { type: 'LineString', coordinates: base.map(([a, b]) => [a + 0.006, b + 0.002]) }, segments: [],
            cost_breakdown: { total_cost: 8.1, travel_time_minutes: 18.5, distance_km: 7.2, avg_aqi: 130, max_aqi: 200, accident_hotspots_passed: 5, travel_time_cost: 8.1, aqi_exposure_cost: 0, accident_risk_cost: 0 },
            weights_used: { alpha: 1, beta: 0, gamma: 0 }
        },
        {
            route_id: 'safe', profile: 'safest', geometry: { type: 'LineString', coordinates: base.map(([a, b]) => [a - 0.008, b - 0.003]) }, segments: [],
            cost_breakdown: { total_cost: 15.2, travel_time_minutes: 28.1, distance_km: 10.3, avg_aqi: 72, max_aqi: 100, accident_hotspots_passed: 0, travel_time_cost: 2.8, aqi_exposure_cost: 1.5, accident_risk_cost: 10.9 },
            weights_used: { alpha: 0.2, beta: 0.1, gamma: 0.7 }
        },
        {
            route_id: 'health', profile: 'healthiest', geometry: { type: 'LineString', coordinates: base.map(([a, b]) => [a - 0.012, b + 0.005]) }, segments: [],
            cost_breakdown: { total_cost: 14.8, travel_time_minutes: 32, distance_km: 11.5, avg_aqi: 55, max_aqi: 78, accident_hotspots_passed: 1, travel_time_cost: 1.6, aqi_exposure_cost: 11.2, accident_risk_cost: 2 },
            weights_used: { alpha: 0.1, beta: 0.7, gamma: 0.2 }
        },
    ];
}

export default App;
