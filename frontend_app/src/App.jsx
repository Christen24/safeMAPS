import { useState, useCallback, useEffect, useRef } from 'react';
import Sidebar from './components/Sidebar';
import MapView from './components/MapView';
import LandingPage from './components/LandingPage';
import GreenScore, { SESSION_ID } from './components/GreenScore';
import './index.css';
import 'leaflet/dist/leaflet.css';

const API_BASE = '/api';

const PRESET_WEIGHTS = {
    fastest:    { alpha: 1.0, beta: 0.0, gamma: 0.0 },
    safest:     { alpha: 0.2, beta: 0.1, gamma: 0.7 },
    healthiest: { alpha: 0.1, beta: 0.7, gamma: 0.2 },
    balanced:   { alpha: 0.4, beta: 0.3, gamma: 0.3 },
};

// ── Nav bar (extracted for reuse across views) ─────────────────
function NavBar({ view, setView, handleShowAQI }) {
    return (
        <div className="nav-bar">
            {/* Brand */}
            <div className="nav-brand">
                <div className="nav-logo">
                    <div className="nav-hex" />
                    <span className="nav-wordmark">SafeMAPS</span>
                </div>
                <span className="nav-system-label">BLR HEALTH ROUTING · v0.4</span>
            </div>

            {/* Tabs */}
            <div className="nav-tabs">
                {[
                    { id: 'dashboard',   label: 'Dashboard',   icon: '▣' },
                    { id: 'heatmaps',    label: 'Heatmaps',    icon: '◈' },
                    { id: 'greenscore',  label: 'Green Score', icon: '◆' },
                ].map(tab => (
                    <button
                        key={tab.id}
                        className={`nav-tab ${view === tab.id ? 'active' : ''}`}
                        onClick={() => {
                            if (tab.id === 'heatmaps') handleShowAQI(true);
                            setView(tab.id);
                        }}
                    >
                        <span>{tab.icon}</span>
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Live readout */}
            <div className="nav-readout">
                <div className="readout-dot" />
                <div className="readout-item">
                    <span className="readout-value">LIVE</span>
                    <span className="readout-label">Data feed</span>
                </div>
                <div className="readout-item">
                    <span className="readout-value">98.2</span>
                    <span className="readout-label">Safety idx</span>
                </div>
            </div>
        </div>
    );
}

export default function App() {
    const [view, setView]                     = useState('landing');
    const [origin, setOrigin]                 = useState({ lat: '', lon: '' });
    const [destination, setDestination]       = useState({ lat: '', lon: '' });
    const [profile, setProfile]               = useState('safest');
    const [weights, setWeights]               = useState({ alpha: 0.20, beta: 0.10, gamma: 0.70 });
    const [departureTime, setDepartureTime]   = useState(null);
    const [routes, setRoutes]                 = useState([]);
    const [selectedRoute, setSelectedRoute]   = useState(null);
    const [loading, setLoading]               = useState(false);
    const [error, setError]                   = useState(null);
    const [showAQI, setShowAQI]               = useState(false);
    const [showBlackspots, setShowBlackspots] = useState(true);
    const [aqiData, setAqiData]               = useState(null);
    const [loadingAQI, setLoadingAQI]         = useState(false);
    const [blackspotData, setBlackspotData]   = useState(null);
    const [mapBounds, setMapBounds]           = useState(null);

    const fetchAQI = useCallback(async (bounds) => {
        if (!bounds) return;
        setLoadingAQI(true);
        try {
            const p = new URLSearchParams({
                min_lat: bounds.south, max_lat: bounds.north,
                min_lon: bounds.west,  max_lon: bounds.east,
            });
            const resp = await fetch(`${API_BASE}/aqi/heatmap?${p}`);
            if (resp.ok) setAqiData(await resp.json());
        } catch (err) { console.warn('AQI fetch failed:', err.message); }
        finally { setLoadingAQI(false); }
    }, []);

    const handleBoundsChange = useCallback((bounds) => {
        setMapBounds(bounds);
        if (showAQI) fetchAQI(bounds);
    }, [showAQI, fetchAQI]);

    const handleShowAQI = useCallback((val) => {
        setShowAQI(val);
        if (val) fetchAQI(mapBounds);
    }, [mapBounds, fetchAQI]);

    const fetchBlackspots = useCallback(async () => {
        try {
            const p = new URLSearchParams({
                min_lat: 12.85, max_lat: 13.15,
                min_lon: 77.45, max_lon: 77.78,
            });
            const resp = await fetch(`${API_BASE}/safety/blackspots?${p}`);
            if (resp.ok) setBlackspotData(await resp.json());
        } catch (err) { console.warn('Blackspot fetch failed:', err.message); }
    }, []);

    useEffect(() => {
        if (view === 'dashboard') fetchBlackspots();
    }, [view, fetchBlackspots]);

    const isFirstRender = useRef(true);
    useEffect(() => {
        if (isFirstRender.current) { isFirstRender.current = false; return; }
        setRoutes([]); setSelectedRoute(null); setError(null);
    }, [origin.lat, origin.lon, destination.lat, destination.lon, departureTime]);

    const handleProfileChange = useCallback((newProfile) => {
        setProfile(newProfile);
        const preset = PRESET_WEIGHTS[newProfile];
        if (preset) setWeights({ alpha: preset.alpha, beta: preset.beta, gamma: preset.gamma });
    }, []);

    const isCustomWeight = useCallback(() => {
        const preset = PRESET_WEIGHTS[profile];
        if (!preset) return true;
        return (
            Math.abs(weights.alpha - preset.alpha) > 0.01 ||
            Math.abs(weights.beta  - preset.beta)  > 0.01 ||
            Math.abs(weights.gamma - preset.gamma)  > 0.01
        );
    }, [profile, weights]);

    const recordTrip = useCallback(async (route) => {
        if (!route) return;
        const cb = route.cost_breakdown;
        try {
            await fetch(`${API_BASE}/user/trips`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Session-ID': SESSION_ID },
                body: JSON.stringify({
                    origin_lat: +origin.lat, origin_lon: +origin.lon,
                    dest_lat: +destination.lat, dest_lon: +destination.lon,
                    profile: route.profile,
                    distance_km: cb.distance_km,
                    travel_time_min: cb.travel_time_minutes,
                    avg_aqi: cb.avg_aqi,
                    aqi_exposure_integral: cb.aqi_exposure_cost * 500.0 /
                        Math.max(route.weights_used?.beta ?? 0.3, 0.01),
                    hotspots_passed: cb.accident_hotspots_passed,
                }),
            });
        } catch (err) { console.warn('Trip record failed:', err.message); }
    }, [origin, destination]);

    const computeRoute = useCallback(async () => {
        if (!origin.lat || !origin.lon || !destination.lat || !destination.lon) {
            setError('Enter valid coordinates for both points.');
            return;
        }
        setLoading(true); setError(null);
        try {
            let chosen = null;
            if (isCustomWeight()) {
                const body = {
                    origin: { lat: +origin.lat, lon: +origin.lon },
                    destination: { lat: +destination.lat, lon: +destination.lon },
                    profile,
                    alpha: weights.alpha, beta: weights.beta, gamma: weights.gamma,
                    use_custom_weights: true,
                };
                if (departureTime) body.departure_time = departureTime;
                const resp = await fetch(`${API_BASE}/route`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!resp.ok) throw new Error((await resp.json()).detail || 'Route failed');
                const route = await resp.json();
                setRoutes([route]); setSelectedRoute(route); chosen = route;
            } else {
                const params = new URLSearchParams({
                    origin_lat: origin.lat, origin_lon: origin.lon,
                    dest_lat: destination.lat, dest_lon: destination.lon,
                });
                if (departureTime) params.set('departure_time', departureTime);
                const resp = await fetch(`${API_BASE}/route/compare?${params}`);
                if (!resp.ok) throw new Error((await resp.json()).detail || 'Route failed');
                const data = await resp.json();
                setRoutes(data.routes);
                const sel = data.routes.find(r => r.profile === profile) || data.routes[0];
                setSelectedRoute(sel); chosen = sel;
            }
            if (chosen) recordTrip(chosen);
        } catch (err) {
            setError(err.message || 'Route computation failed');
            const mocks = getMockRoutes();
            setRoutes(mocks);
            setSelectedRoute(mocks.find(r => r.profile === profile) || mocks[0]);
        } finally { setLoading(false); }
    }, [origin, destination, profile, weights, departureTime, isCustomWeight, recordTrip]);

    const handleMapClick = useCallback((latlng) => {
        if (!origin.lat) {
            setOrigin({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
        } else if (!destination.lat) {
            setDestination({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
        } else {
            setOrigin({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
            setDestination({ lat: '', lon: '' });
            setRoutes([]); setSelectedRoute(null); setError(null);
        }
    }, [origin, destination]);

    const swapPoints = useCallback(() => {
        setOrigin(destination); setDestination(origin);
    }, [origin, destination]);

    if (view === 'landing') {
        return <LandingPage onStart={() => setView('dashboard')} />;
    }

    if (view === 'greenscore') {
        return (
            <div className="app" style={{ flexDirection: 'column' }}>
                <NavBar view={view} setView={setView} handleShowAQI={handleShowAQI} />
                <div className="main-content gs-page" style={{ marginTop: 0 }}>
                    <GreenScore />
                </div>
            </div>
        );
    }

    return (
        <div className="app" style={{ flexDirection: 'column' }}>
            <NavBar view={view} setView={setView} handleShowAQI={handleShowAQI} />
            <div className="main-content">
                <Sidebar
                    origin={origin} destination={destination}
                    setOrigin={setOrigin} setDestination={setDestination}
                    profile={profile} setProfile={handleProfileChange}
                    weights={weights} setWeights={setWeights}
                    departureTime={departureTime} setDepartureTime={setDepartureTime}
                    routes={routes} selectedRoute={selectedRoute}
                    setSelectedRoute={setSelectedRoute}
                    onCompute={computeRoute} onSwap={swapPoints}
                    loading={loading} error={error}
                />
                <MapView
                    origin={origin} destination={destination}
                    selectedRoute={selectedRoute} routes={routes}
                    showAQI={showAQI} setShowAQI={handleShowAQI}
                    showBlackspots={showBlackspots} setShowBlackspots={setShowBlackspots}
                    aqiData={aqiData} blackspotData={blackspotData}
                    loadingAQI={loadingAQI}
                    loading={loading} onMapClick={handleMapClick}
                    onBoundsChange={handleBoundsChange}
                />
            </div>
        </div>
    );
}

function getMockRoutes() {
    const base = [
        [77.5946, 12.9716],[77.5980,12.9700],[77.6020,12.9660],
        [77.6060,12.9580],[77.6101,12.9352],[77.6150,12.9300],[77.6230,12.9170],
    ];
    return [
        { route_id:'bal', profile:'balanced',   geometry:{type:'LineString',coordinates:base},                               segments:[], cost_breakdown:{total_cost:12.5,travel_time_minutes:22.3,distance_km:8.7,  avg_aqi:95, max_aqi:145,accident_hotspots_passed:2,travel_time_cost:5,  aqi_exposure_cost:4.2,accident_risk_cost:3.3}, weights_used:{alpha:0.4,beta:0.3,gamma:0.3} },
        { route_id:'fast',profile:'fastest',    geometry:{type:'LineString',coordinates:base.map(([a,b])=>[a+0.006,b+0.002])}, segments:[], cost_breakdown:{total_cost:8.1, travel_time_minutes:18.5,distance_km:7.2,  avg_aqi:130,max_aqi:200,accident_hotspots_passed:5,travel_time_cost:8.1,aqi_exposure_cost:0,  accident_risk_cost:0},   weights_used:{alpha:1,  beta:0,  gamma:0}   },
        { route_id:'safe',profile:'safest',     geometry:{type:'LineString',coordinates:base.map(([a,b])=>[a-0.008,b-0.003])}, segments:[], cost_breakdown:{total_cost:15.2,travel_time_minutes:28.1,distance_km:10.3,avg_aqi:72, max_aqi:100,accident_hotspots_passed:0,travel_time_cost:2.8,aqi_exposure_cost:1.5,accident_risk_cost:10.9},weights_used:{alpha:0.2,beta:0.1,gamma:0.7} },
        { route_id:'hlth',profile:'healthiest', geometry:{type:'LineString',coordinates:base.map(([a,b])=>[a-0.012,b+0.005])}, segments:[], cost_breakdown:{total_cost:14.8,travel_time_minutes:32,  distance_km:11.5,avg_aqi:55, max_aqi:78, accident_hotspots_passed:1,travel_time_cost:1.6,aqi_exposure_cost:11.2,accident_risk_cost:2},  weights_used:{alpha:0.1,beta:0.7,gamma:0.2} },
    ];
}
