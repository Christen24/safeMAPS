import { useState, useCallback, useEffect, useRef, memo } from 'react';
import Sidebar from './components/Sidebar';
import MapView from './components/MapView';
import LandingPage from './components/LandingPage';
import GreenScore, { SESSION_ID } from './components/GreenScore';
import PWAInstallPrompt from './components/PWAInstallPrompt';
import AIDemo from './pages/AIDemo';
import { decodeURLToRoute, encodeRouteToURL, buildShareURL } from './utils/shareURL';
import 'leaflet/dist/leaflet.css';
import './index.css';

const API_BASE = '/api';

const PRESET_WEIGHTS = {
    fastest:    { alpha: 1.0, beta: 0.0, gamma: 0.0 },
    safest:     { alpha: 0.2, beta: 0.1, gamma: 0.7 },
    healthiest: { alpha: 0.1, beta: 0.7, gamma: 0.2 },
    balanced:   { alpha: 0.4, beta: 0.3, gamma: 0.3 },
};

// ── Nav bar (extracted for reuse across views) ─────────────────
function haversineMeters(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const toRad = d => d * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
}

function bearingDeg(lat1, lon1, lat2, lon2) {
    const toRad = d => d * Math.PI / 180;
    const y = Math.sin(toRad(lon2 - lon1)) * Math.cos(toRad(lat2));
    const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
        Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(toRad(lon2 - lon1));
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function useIST() {
    const [time, setTime] = useState(() =>
        new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata', hour12: false })
    );
    useEffect(() => {
        const tick = setInterval(() =>
            setTime(new Date().toLocaleTimeString('en-IN', {
                hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata', hour12: false
            })), 30_000);
        return () => clearInterval(tick);
    }, []);
    return time;
}

const NavBar = memo(function NavBar({ view, setView, handleShowAQI, isOffline, incidentCount }) {
    const istTime = useIST();
    return (
        <div className="nav-bar">
            {/* Brand */}
            <div className="nav-brand">
                <div className="nav-logo">
                    <div className="nav-hex" />
                    <span className="nav-wordmark">SafeMAPS</span>
                </div>
                <span className="nav-system-label">BLR HEALTH ROUTING · v0.5</span>
            </div>
            {/* Tabs */}
            <div className="nav-tabs">
                {[
                    { id: 'dashboard',   label: 'Dashboard',   icon: '▣' },
                    { id: 'heatmaps',    label: 'Heatmaps',    icon: '◈' },
                    { id: 'greenscore',  label: 'Green Score', icon: '◆' },
                    { id: 'ai',          label: 'Plan Route',  icon: '✦' },
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
                <div className={`readout-dot ${isOffline ? 'offline' : ''}`} />
                <div className="readout-item">
                    <span className="readout-value">{isOffline ? 'DEMO' : 'LIVE'}</span>
                    <span className="readout-label">{isOffline ? 'No backend' : 'Data feed'}</span>
                </div>
                {incidentCount > 0 && (
                    <div className="readout-item">
                        <span className="readout-value nav-incident-badge">{incidentCount}</span>
                        <span className="readout-label">incidents</span>
                    </div>
                )}
                <div className="readout-item">
                    <span className="readout-value nav-clock">{istTime}</span>
                    <span className="readout-label">IST</span>
                </div>
            </div>
        </div>
    );
});

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
    const [showIncidents, setShowIncidents]   = useState(true);
    const [aqiData, setAqiData]               = useState(null);
    const [loadingAQI, setLoadingAQI]         = useState(false);
    const [locatingUser, setLocatingUser]     = useState(false);
    const [blackspotData, setBlackspotData]   = useState(null);
    const [mapBounds, setMapBounds]           = useState(null);
    const [isOffline, setIsOffline]           = useState(false);
    const [navigating, setNavigating]         = useState(false);
    const [livePosition, setLivePosition]     = useState(null); // {lat, lon, heading, accuracy}
    const [currentStep, setCurrentStep]       = useState(0);
    const [navError, setNavError]             = useState(null);
    const [recenterTick, setRecenterTick]     = useState(0); // bump to signal MapView to recenter

    const watchIdRef     = useRef(null);
    const lastFixRef     = useRef(null); // previous {lat, lon} — used to derive heading when coords.heading is null
    // ── Stable refs so callbacks don't recreate on every state change ─
    const showAQIRef    = useRef(showAQI);
    const fetchAQIRef   = useRef(null);
    const originRef     = useRef(origin);
    const destinationRef = useRef(destination);
    const selectedRouteRef = useRef(null);
    useEffect(() => { showAQIRef.current = showAQI; }, [showAQI]);
    useEffect(() => { originRef.current = origin; }, [origin]);
    useEffect(() => { destinationRef.current = destination; }, [destination]);
    useEffect(() => { selectedRouteRef.current = selectedRoute; }, [selectedRoute]);
    const [bannerDismissed, setBannerDismissed] = useState(false);
    const [shareCopied, setShareCopied]       = useState(false);
    const [incidents, setIncidents]           = useState(null);
    const [loadingIncidents, setLoadingIncidents] = useState(false);
    const pendingAutoCompute                  = useRef(false);

    // ── Decode URL params on mount → auto-fill + auto-compute ─────────
    useEffect(() => {
        const decoded = decodeURLToRoute();
        if (decoded) {
            setOrigin(decoded.origin);
            setDestination(decoded.destination);
            handleProfileChange(decoded.profile);
            if (decoded.departureTime) setDepartureTime(decoded.departureTime);
            setView('dashboard');
            pendingAutoCompute.current = true;
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // ── Auto-compute once view is 'dashboard' and coords are loaded ────
    const autoComputeRan = useRef(false);
    useEffect(() => {
        if (pendingAutoCompute.current && view === 'dashboard' &&
            origin.lat && destination.lat && !autoComputeRan.current) {
            autoComputeRan.current = true;
            pendingAutoCompute.current = false;
            computeRouteWithCoords(origin, destination, profile);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [view, origin.lat, destination.lat]);

    // ── Copy share link to clipboard ──────────────────────────────────
    const handleShare = useCallback(() => {
        if (!origin.lat || !destination.lat) return;
        const url = buildShareURL(origin, destination, profile, departureTime);
        navigator.clipboard.writeText(url).then(() => {
            setShareCopied(true);
            setTimeout(() => setShareCopied(false), 2000);
        }).catch(() => {
            // fallback: select text
            prompt('Copy this link:', url);
        });
    }, [origin, destination, profile, departureTime]);

    useEffect(() => {
        let cancelled = false;
        const checkHealth = async () => {
            try {
                const resp = await fetch(`${API_BASE.replace('/api', '')}/health`, {
                    signal: AbortSignal.timeout(4000),
                });
                const data = await resp.json().catch(() => ({}));
                if (!cancelled) {
                    setIsOffline(!resp.ok || data.database === 'disconnected');
                }
            } catch {
                if (!cancelled) setIsOffline(true);
            }
        };
        checkHealth();
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        let cancelled = false;
        const fetchIncidents = async () => {
            if (!cancelled) setLoadingIncidents(true);
            try {
                const resp = await fetch(`${API_BASE}/incidents/active?limit=300`);
                if (resp.ok) {
                    const data = await resp.json();
                    if (!cancelled) setIncidents(data);
                }
            } catch (err) { console.warn('Incidents fetch failed:', err.message); }
            finally {
                if (!cancelled) setLoadingIncidents(false);
            }
        };
        fetchIncidents();
        const id = setInterval(fetchIncidents, 10 * 60 * 1000);
        return () => {
            cancelled = true;
            clearInterval(id);
        };
    }, []);

    // Keep fetchAQIRef in sync so handleBoundsChange can call latest version
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
    // keep fetchAQIRef in sync so handleBoundsChange can call latest version
    useEffect(() => { fetchAQIRef.current = fetchAQI; }, [fetchAQI]);

    // Stable callback — does NOT list showAQI in deps; reads via ref instead.
    // This prevents useMapEvents from getting a new function ref on every
    // showAQI toggle, which was the root cause of React error #310.
    const handleBoundsChange = useCallback((bounds) => {
        setMapBounds(bounds);
        if (showAQIRef.current && fetchAQIRef.current) fetchAQIRef.current(bounds);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);  // intentionally empty — reads mutable refs

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

    const _fetchRoute = useCallback(async (org, dst, prof, wts, depTime, custom) => {
        setLoading(true); setError(null);
        try {
            let chosen = null;
            if (custom) {
                const body = {
                    origin: { lat: +org.lat, lon: +org.lon },
                    destination: { lat: +dst.lat, lon: +dst.lon },
                    profile: prof,
                    alpha: wts.alpha, beta: wts.beta, gamma: wts.gamma,
                    use_custom_weights: true,
                };
                if (depTime) body.departure_time = depTime;
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
                    origin_lat: org.lat, origin_lon: org.lon,
                    dest_lat: dst.lat, dest_lon: dst.lon,
                });
                if (depTime) params.set('departure_time', depTime);
                const resp = await fetch(`${API_BASE}/route/compare?${params}`);
                if (!resp.ok) throw new Error((await resp.json()).detail || 'Route failed');
                const data = await resp.json();
                setRoutes(data.routes);
                const sel = data.routes.find(r => r.profile === prof) || data.routes[0];
                setSelectedRoute(sel); chosen = sel;
            }
            if (chosen) {
                recordTrip(chosen);
                encodeRouteToURL(org, dst, prof, depTime);
            }
            return true;
        } catch (err) {
            console.error('Route computation failed:', err);
            setError((err.message || 'Route computation failed') + ' — showing no route rather than a placeholder.');
            setRoutes([]);
            setSelectedRoute(null);
            return false;
        } finally { setLoading(false); }
    }, [recordTrip]);

    const computeRoute = useCallback(async () => {
        if (!origin.lat || !origin.lon || !destination.lat || !destination.lon) {
            setError('Enter valid coordinates for both points.');
            return;
        }
        await _fetchRoute(origin, destination, profile, weights, departureTime, isCustomWeight());
    }, [origin, destination, profile, weights, departureTime, isCustomWeight, _fetchRoute]);

    const computeRouteWithCoords = useCallback(async (org, dst, prof) => {
        if (!org.lat || !org.lon || !dst.lat || !dst.lon) return false;
        const preset = PRESET_WEIGHTS[prof] || weights;
        return await _fetchRoute(org, dst, prof, preset, departureTime, false);
    }, [weights, departureTime, _fetchRoute]);

    const handleUseCurrentLocation = useCallback(() => {
        if (!navigator.geolocation) {
            setError('Live location is not supported by this browser.');
            return;
        }

        setLocatingUser(true);
        setError(null);
        navigator.geolocation.getCurrentPosition(
            async (pos) => {
                const accuracy = pos.coords.accuracy; // metres — browser-reported confidence radius
                const nextOrigin = {
                    lat: pos.coords.latitude.toFixed(6),
                    lon: pos.coords.longitude.toFixed(6),
                };
                setOrigin(nextOrigin);
                setLocatingUser(false);

                if (destinationRef.current.lat && destinationRef.current.lon) {
                    const ok = await computeRouteWithCoords(nextOrigin, destinationRef.current, profile);
                    // Bug fix: without a GPS chip, browser geolocation falls back
                    // to WiFi/IP-based positioning, which can be off by hundreds
                    // of metres to a few km — easily past the 500m snap radius
                    // the backend uses to avoid snapping to a road nowhere near
                    // where you actually are. A manual map click never hits this
                    // because you're always choosing a point visibly near a road.
                    // When the route fails AND the browser itself reported low
                    // confidence, say so specifically instead of the generic
                    // "no road found" message.
                    if (!ok && accuracy > 300) {
                        setError(
                            `Your device reported an imprecise location (~${Math.round(accuracy)}m accuracy), ` +
                            `which is likely why no nearby road was found. Try again near a window/outdoors for a ` +
                            `better fix, or drag the origin marker to your actual position on the map instead.`
                        );
                    }
                }
            },
            (geoError) => {
                setLocatingUser(false);
                const message = geoError.code === geoError.PERMISSION_DENIED
                    ? 'Location permission was denied. Allow location access and try again.'
                    : 'Could not fetch your live location. Check GPS or browser permissions.';
                setError(message);
            },
            { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
        );
    }, [computeRouteWithCoords, profile]);

    // ── Journey mode: live tracking + turn-by-turn ──────────────────
    // Design notes:
    // - Uses watchPosition (continuous), not getCurrentPosition (one-shot) —
    //   "Use my location" above is a one-time origin pick; this is ongoing.
    // - The arrow is drawn at the raw GPS fix, not snapped onto the route
    //   line. Snapping to the nearest point on the polyline is a real
    //   improvement (GPS jitter off a road edge looks bad) but adds a
    //   nearest-point-on-polyline projection step; left as a follow-up
    //   rather than risking an untested projection bug in this pass.
    // - heading: coords.heading is frequently null on desktop/stationary
    //   devices. Falls back to the bearing between this fix and the last
    //   one, so the arrow still rotates sensibly while walking/driving
    //   even when the browser doesn't report a heading directly.
    const handlePositionUpdate = useCallback((pos) => {
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        let heading = pos.coords.heading;

        if ((heading === null || Number.isNaN(heading)) && lastFixRef.current) {
            const prev = lastFixRef.current;
            const moved = haversineMeters(prev.lat, prev.lon, lat, lon);
            if (moved > 3) { // ignore GPS jitter under ~3m — bearing would be noise
                heading = bearingDeg(prev.lat, prev.lon, lat, lon);
            } else {
                heading = prev.heading ?? 0;
            }
        }
        heading = heading ?? 0;
        lastFixRef.current = { lat, lon, heading };

        setLivePosition({ lat, lon, heading, accuracy: pos.coords.accuracy });
        setNavError(null);

        // Advance to the next instruction once we're close to the start
        // of the step after the current one (25m threshold — rough, but
        // avoids flapping back and forth right at a turn).
        setCurrentStep(prevStep => {
            const route = selectedRouteRef.current;
            const steps = route?.instructions;
            if (!steps || prevStep >= steps.length - 1) return prevStep;
            const next = steps[prevStep + 1];
            if (!next?.location) return prevStep;
            const d = haversineMeters(lat, lon, next.location.lat, next.location.lon);
            return d < 25 ? prevStep + 1 : prevStep;
        });
    }, []);

    const startJourney = useCallback(() => {
        if (!selectedRoute) {
            setNavError('Compute a route before starting a journey.');
            return;
        }
        if (!navigator.geolocation) {
            setNavError('Live location is not supported by this browser.');
            return;
        }
        setCurrentStep(0);
        setNavError(null);
        setNavigating(true);
        watchIdRef.current = navigator.geolocation.watchPosition(
            handlePositionUpdate,
            (geoError) => {
                const message = geoError.code === geoError.PERMISSION_DENIED
                    ? 'Location permission was denied. Allow location access to navigate.'
                    : 'Lost live location signal. Check GPS/permissions.';
                setNavError(message);
            },
            { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 }
        );
    }, [selectedRoute, handlePositionUpdate]);

    const stopJourney = useCallback(() => {
        if (watchIdRef.current !== null) {
            navigator.geolocation.clearWatch(watchIdRef.current);
            watchIdRef.current = null;
        }
        setNavigating(false);
        setLivePosition(null);
        setCurrentStep(0);
        lastFixRef.current = null;
    }, []);

    // Clean up the GPS watch if the component unmounts mid-journey
    useEffect(() => {
        return () => {
            if (watchIdRef.current !== null) {
                navigator.geolocation.clearWatch(watchIdRef.current);
            }
        };
    }, []);

    // "My Location" recenter button — works whether or not a journey is
    // active. During a journey, reuses the live-tracked position (no
    // extra GPS call); otherwise takes a fresh one-shot fix.
    const handleRecenter = useCallback(() => {
        if (livePosition) {
            setRecenterTick(t => t + 1);
            return;
        }
        if (!navigator.geolocation) {
            setNavError('Live location is not supported by this browser.');
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                setLivePosition({
                    lat: pos.coords.latitude,
                    lon: pos.coords.longitude,
                    heading: pos.coords.heading ?? 0,
                    accuracy: pos.coords.accuracy,
                });
                setRecenterTick(t => t + 1);
            },
            () => setNavError('Could not fetch your current location.'),
            { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
        );
    }, [livePosition]);

    // Stable callback — reads origin/destination via refs to avoid
    // giving useMapEvents a new function ref on every click (React #310).
    const handleMapClick = useCallback((latlng) => {
        const o = originRef.current;
        const d = destinationRef.current;
        if (!o.lat) {
            setOrigin({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
        } else if (!d.lat) {
            setDestination({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
        } else {
            setOrigin({ lat: latlng.lat.toFixed(6), lon: latlng.lng.toFixed(6) });
            setDestination({ lat: '', lon: '' });
            setRoutes([]); setSelectedRoute(null); setError(null);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);  // intentionally empty — reads mutable refs

    const swapPoints = useCallback(() => {
        setOrigin(destination); setDestination(origin);
    }, [origin, destination]);

    // ── Load a saved commute (one-tap re-route) ────────────────────
    const handleLoadCommute = useCallback((comOrigin, comDest, comProfile) => {
        setOrigin(comOrigin);
        setDestination(comDest);
        handleProfileChange(comProfile);
        setRoutes([]); setSelectedRoute(null); setError(null);
        computeRouteWithCoords(comOrigin, comDest, comProfile);
    }, [handleProfileChange, computeRouteWithCoords]);

    if (view === 'landing') {
        return <LandingPage onStart={() => setView('dashboard')} onLaunchAI={() => setView('ai')} />;
    }

    const showBanner = isOffline && !bannerDismissed;

    if (view === 'ai') {
        return <AIDemo onBack={() => setView('dashboard')} />;
    }

    if (view === 'greenscore') {
        return (
            <div className="app" style={{ flexDirection: 'column' }}>
                {showBanner && (
                    <OfflineBanner onDismiss={() => setBannerDismissed(true)} />
                )}
                <NavBar view={view} setView={setView} handleShowAQI={handleShowAQI} isOffline={isOffline} incidentCount={incidents?.total ?? incidents?.features?.length ?? 0} />
                <div className="main-content gs-page" style={{ marginTop: 0 }}>
                    <GreenScore />
                </div>
            </div>
        );
    }

    return (
        <div className="app" style={{ flexDirection: 'column' }}>
            <PWAInstallPrompt />
            {showBanner && (
                <OfflineBanner onDismiss={() => setBannerDismissed(true)} />
            )}
            <NavBar view={view} setView={setView} handleShowAQI={handleShowAQI} isOffline={isOffline} incidentCount={incidents?.total ?? incidents?.features?.length ?? 0} />
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
                    locatingUser={locatingUser}
                    onUseCurrentLocation={handleUseCurrentLocation}
                    onShare={handleShare} shareCopied={shareCopied}
                    onLoadCommute={handleLoadCommute}
                />
                <MapView
                    origin={origin} destination={destination}
                    selectedRoute={selectedRoute} routes={routes}
                    showAQI={showAQI} setShowAQI={handleShowAQI}
                    showBlackspots={showBlackspots} setShowBlackspots={setShowBlackspots}
                    showIncidents={showIncidents} setShowIncidents={setShowIncidents}
                    aqiData={aqiData} blackspotData={blackspotData}
                    loadingAQI={loadingAQI}
                    incidentData={incidents}
                    loadingIncidents={loadingIncidents}
                    loading={loading} onMapClick={handleMapClick}
                    onBoundsChange={handleBoundsChange}
                    navigating={navigating}
                    livePosition={livePosition}
                    currentStep={currentStep}
                    navError={navError}
                    recenterTick={recenterTick}
                    onStartJourney={startJourney}
                    onStopJourney={stopJourney}
                    onRecenter={handleRecenter}
                    onSelectRoute={setSelectedRoute}
                />
            </div>
        </div>
    );
}

const OfflineBanner = memo(function OfflineBanner({ onDismiss }) {
    return (
        <div className="offline-banner" role="alert">
            <span className="offline-banner-icon">⚠</span>
            <span className="offline-banner-text">
                Live data unavailable — showing demo routes.
                Backend may still be starting up.
            </span>
            <button className="offline-banner-dismiss" onClick={onDismiss} aria-label="Dismiss">
                ✕
            </button>
        </div>
    );
});


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
