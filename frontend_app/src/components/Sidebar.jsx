import { useState, useCallback, useRef, useEffect, memo } from 'react';
import { aqiColor } from './MapView';
import SavedCommutesPanel from './SavedCommutesPanel';

const PROFILES = [
    { id: 'fastest',    label: 'Fastest',    sub: 'Min. Time',      icon: '⚡', color: 'var(--ice)'    },
    { id: 'safest',     label: 'Safest',     sub: 'Max. Security',  icon: '🛡️', color: 'var(--acid)'   },
    { id: 'healthiest', label: 'Healthiest', sub: 'Low AQI',        icon: '🫁', color: 'var(--amber)'  },
    { id: 'balanced',   label: 'Balanced',   sub: 'Weighted',       icon: '⚖️', color: 'var(--violet)' },
];

const NOMINATIM = 'https://nominatim.openstreetmap.org/search';

async function geocode(query) {
    if (!query || query.length < 3) return [];
    // Fix S1: detect raw lat/lon — return directly without hitting Nominatim
    const coordMatch = query.trim().match(/^(-?\d{1,3}\.?\d*)[,\s]+(-?\d{1,3}\.?\d*)$/);
    if (coordMatch) {
        const lat = coordMatch[1], lon = coordMatch[2];
        return [{ lat, lon, display_name: lat + ', ' + lon, road: null, suburb: null, city: 'Bangalore' }];
    }
    try {
        // Fix S1: viewbox bias instead of appending ", Bangalore" to query.
        // Appending city corrupts Nominatim's parser for specific addresses.
        const params = new URLSearchParams({
            q: query,
            format: 'json',
            limit: 6,
            addressdetails: 1,
            viewbox: '77.45,12.85,77.78,13.15',
            bounded: 0,
            countrycodes: 'in',
        });
        const resp = await fetch(NOMINATIM + '?' + params, {
            headers: { 'Accept-Language': 'en' },
        });
        if (!resp.ok) return [];
        const results = await resp.json();
        return results.map(({ lat, lon, display_name, address }) => ({
            lat, lon, display_name,
            road:   address?.road || address?.pedestrian || address?.footway,
            suburb: address?.suburb || address?.neighbourhood,
            city:   address?.city  || address?.town || 'Bangalore',
        }));
    } catch { return []; }
}

function reverseGeocode(lat, lon) {
    return fetch(
        `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&accept-language=en`,
        { headers: { 'Accept-Language': 'en' } }
    ).then(r => r.ok ? r.json() : null).catch(() => null);
}

// ── Bug 4 fix: smarter display name truncation ────────────────
// Nominatim returns "Road Name, Area, City, State, Country"
// We show "Road Name, Area" as name and "City, State" as detail
// This disambiguates e.g. two "1st Cross Road" results in different areas
// ── Recent searches (localStorage, max 5 per field) ──────────────────
const RECENTS_KEY = 'safemaps_recent_places';

function loadRecents() {
    try { return JSON.parse(localStorage.getItem(RECENTS_KEY) || '[]'); }
    catch { return []; }
}

function saveRecent(item) {
    const existing = loadRecents().filter(r => r.lat !== item.lat || r.lon !== item.lon);
    const updated  = [item, ...existing].slice(0, 5);
    try { localStorage.setItem(RECENTS_KEY, JSON.stringify(updated)); }
    catch { /* storage full — ignore */ }
}


function formatSuggestion(display_name, addressParts = {}) {
    const parts = display_name.split(',').map(p => p.trim()).filter(Boolean);
    if (addressParts.road && addressParts.suburb) {
        return {
            name:   `${addressParts.road}, ${addressParts.suburb}`,
            detail: addressParts.city || parts.slice(2, 3).join(''),
        };
    }
    return {
        name:   parts.slice(0, 2).join(', '),
        detail: parts.slice(2, 4).join(', '),
    };
}

const PlaceInput = memo(function PlaceInput({ placeholder, value, onSelect, indicator }) {
    const [query, setQuery]               = useState('');
    const [suggestions, setSuggestions]   = useState([]);
    const [recents, setRecents]           = useState(loadRecents);
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [displayName, setDisplayName]   = useState('');
    const [activeIdx, setActiveIdx]       = useState(-1);
    const [locating, setLocating]         = useState(false);
    const [searching, setSearching]       = useState(false);
    const debounceRef  = useRef(null);
    const wrapperRef   = useRef(null);
    const inputRef     = useRef(null);
    const requestIdRef = useRef(0);
    const prevCoordRef = useRef('');

    useEffect(() => {
        const h = (e) => {
            if (wrapperRef.current && !wrapperRef.current.contains(e.target))
                setShowSuggestions(false);
        };
        document.addEventListener('mousedown', h);
        return () => document.removeEventListener('mousedown', h);
    }, []);

    useEffect(() => {
        const coordKey = `${value.lat},${value.lon}`;
        if (value.lat && value.lon) {
            if (coordKey !== prevCoordRef.current) {
                prevCoordRef.current = coordKey;
                setDisplayName((+value.lat).toFixed(4) + ', ' + (+value.lon).toFixed(4));
            }
        } else {
            prevCoordRef.current = '';
            setDisplayName('');
            setQuery('');
        }
    }, [value.lat, value.lon]);

    const handleChange = useCallback((e) => {
        const q = e.target.value;
        setQuery(q);
        setDisplayName('');
        setActiveIdx(-1);
        if (debounceRef.current) clearTimeout(debounceRef.current);
        if (q.length >= 3) {
            const myId = ++requestIdRef.current;
            setSearching(true);
            debounceRef.current = setTimeout(async () => {
                const res = await geocode(q);
                if (myId !== requestIdRef.current) return;
                setSearching(false);
                setSuggestions(res);
                setShowSuggestions(true);
            }, 280);
        } else {
            requestIdRef.current++;
            setSearching(false);
            setSuggestions([]);
            setShowSuggestions(q.length === 0 && recents.length > 0);
        }
    }, [recents]);

    const handleSelect = useCallback((item) => {
        const { name } = formatSuggestion(item.display_name, item);
        prevCoordRef.current = `${item.lat},${item.lon}`;
        setDisplayName(name);
        setQuery('');
        setSuggestions([]);
        setShowSuggestions(false);
        setActiveIdx(-1);
        saveRecent({ lat: item.lat, lon: item.lon, display_name: item.display_name });
        setRecents(loadRecents());
        onSelect({ lat: item.lat, lon: item.lon });
    }, [onSelect]);

    const handleClear = useCallback(() => {
        setDisplayName('');
        setQuery('');
        setSuggestions([]);
        setShowSuggestions(false);
        setActiveIdx(-1);
        prevCoordRef.current = '';
        onSelect({ lat: '', lon: '' });
        inputRef.current?.focus();
    }, [onSelect]);

    const handleGeolocate = useCallback(() => {
        if (!navigator.geolocation) return;
        setLocating(true);
        navigator.geolocation.getCurrentPosition(
            async (pos) => {
                const { latitude: lat, longitude: lon } = pos.coords;
                setLocating(false);
                const data = await reverseGeocode(lat, lon);
                const name = data?.display_name
                    ? formatSuggestion(data.display_name).name
                    : `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
                const item = { lat: String(lat), lon: String(lon), display_name: data?.display_name || name };
                prevCoordRef.current = `${item.lat},${item.lon}`;
                setDisplayName(name);
                setShowSuggestions(false);
                saveRecent(item);
                setRecents(loadRecents());
                onSelect({ lat: item.lat, lon: item.lon });
            },
            () => setLocating(false),
            { timeout: 8000 }
        );
    }, [onSelect]);

    const allItems = query.length >= 3 ? suggestions : recents;

    const handleKeyDown = useCallback((e) => {
        if (!showSuggestions) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveIdx(i => Math.min(i + 1, allItems.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveIdx(i => Math.max(i - 1, -1));
        } else if (e.key === 'Enter' && activeIdx >= 0) {
            e.preventDefault();
            handleSelect(allItems[activeIdx]);
        } else if (e.key === 'Escape') {
            setShowSuggestions(false);
            setActiveIdx(-1);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [showSuggestions, activeIdx, allItems, handleSelect]);

    const inputValue = displayName || query;
    const showClear  = inputValue.length > 0;
    const showRecents = query.length === 0 && recents.length > 0;

    return (
        <div className="place-input-wrapper" ref={wrapperRef}>
            <div className="input-row">
                <div className="input-pip">
                    <span className={`pip-dot ${indicator}`} />
                </div>
                <input
                    ref={inputRef}
                    placeholder={placeholder}
                    value={inputValue}
                    onChange={handleChange}
                    onFocus={() => {
                        if (recents.length > 0 || suggestions.length > 0)
                            setShowSuggestions(true);
                    }}
                    onKeyDown={handleKeyDown}
                    autoComplete="off"
                    spellCheck={false}
                />
                {searching && <span className="input-spinner" title="Searching…">⟳</span>}
                {showClear && !searching && (
                    <button className="input-clear-btn" onClick={handleClear} title="Clear" tabIndex={-1}>×</button>
                )}
                <button
                    className={`input-locate-btn ${locating ? 'locating' : ''}`}
                    onClick={handleGeolocate}
                    title="Use my location"
                    tabIndex={-1}
                >📍</button>
            </div>

            {showSuggestions && allItems.length > 0 && (
                <ul className="geocode-dropdown" role="listbox">
                    {showRecents && (
                        <li className="geocode-section-label">Recent</li>
                    )}
                    {allItems.map((s, i) => {
                        const { name, detail } = formatSuggestion(s.display_name, s);
                        return (
                            <li
                                className={`geocode-option ${i === activeIdx ? 'active' : ''}`}
                                key={`${s.lat}-${s.lon}-${i}`}
                                role="option"
                                aria-selected={i === activeIdx}
                                onMouseEnter={() => setActiveIdx(i)}
                                onClick={() => handleSelect(s)}
                            >
                                <span className="suggestion-icon">
                                    {showRecents && i < recents.length ? '🕐' : '📍'}
                                </span>
                                <span className="suggestion-text">
                                    <span className="suggestion-name">{name}</span>
                                    {detail && <span className="suggestion-detail">{detail}</span>}
                                </span>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
});


export default function Sidebar({
    origin, destination, setOrigin, setDestination,
    profile, setProfile,
    weights, setWeights,
    departureTime, setDepartureTime,
    routes, selectedRoute, setSelectedRoute,
    onCompute, onSwap, loading, error,
    onShare, shareCopied,
    onLoadCommute,
}) {
    const canCompute = origin.lat && origin.lon && destination.lat && destination.lon && !loading;
    const [segmentsExpanded, setSegmentsExpanded] = useState(false);

    // ── Enter key = compute ───────────────────────────────────────────
    useEffect(() => {
        const handler = (e) => {
            if (e.key === 'Enter' && canCompute && document.activeElement?.tagName !== 'INPUT') {
                onCompute();
            }
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [canCompute, onCompute]);

    useEffect(() => { setSegmentsExpanded(false); }, [selectedRoute?.route_id]);

    return (
        <aside className="sidebar">

            {/* ── Saved Commutes ── */}
            <SavedCommutesPanel
                origin={origin}
                destination={destination}
                profile={profile}
                onLoad={onLoadCommute}
                isActive={routes.length > 0}
            />

            {/* ── Route Input ── */}
            <div className="section-header">
                <span className="section-label">Route Input</span>
            </div>

            <div className="route-input-section">
                <PlaceInput
                    placeholder="Search origin…"
                    value={origin}
                    onSelect={setOrigin}
                    indicator="origin"
                />

                <div className="input-connector">
                    <div className="connector-line" />
                    <button className="swap-btn" onClick={onSwap} title="Swap">⇅</button>
                    <div className="connector-line" />
                </div>

                <PlaceInput
                    placeholder="Search destination…"
                    value={destination}
                    onSelect={setDestination}
                    indicator="dest"
                />

                {/* Departure time */}
                <div className="departure-row" style={{ marginTop: 8 }}>
                    <span className="departure-label">DEPART</span>
                    <input
                        type="datetime-local"
                        className="departure-input"
                        value={departureTime || ''}
                        onChange={(e) => setDepartureTime(e.target.value || null)}
                    />
                </div>

                <p className="hint">Tap map to pin · or search by place name</p>
            </div>

            {/* ── Profile ── */}
            <div className="section-header">
                <span className="section-label">Route Profile</span>
                <span className="section-badge">{profile}</span>
            </div>

            <div className="profile-section">
                <div className="profile-grid">
                    {PROFILES.map(p => (
                        <div
                            key={p.id}
                            className={`profile-card ${p.id} ${profile === p.id ? 'active' : ''}`}
                            onClick={() => setProfile(p.id)}
                        >
                            <span className="profile-icon">{p.icon}</span>
                            <span className="profile-name">{p.label}</span>
                            <span className="profile-sub">{p.sub}</span>
                        </div>
                    ))}
                </div>
            </div>

            {/* ── Weights ── */}
            <div className="section-header">
                <span className="section-label">Cost Weights α β γ</span>
            </div>

            <div className="weights-section">
                {[
                    { key: 'alpha', label: '⏱ Travel Time (α)', cls: 'time',  colorCls: 'time'  },
                    { key: 'beta',  label: '🌫 AQI Exposure (β)', cls: 'aqi',  colorCls: 'aqi'   },
                    { key: 'gamma', label: '⚠ Accident Risk (γ)', cls: 'risk', colorCls: 'risk'  },
                ].map(({ key, label, cls }) => (
                    <div className="weight-row" key={key}>
                        <div className="weight-header">
                            <span className="weight-label">{label}</span>
                            <span className={`weight-value ${cls}`}>{weights[key].toFixed(2)}</span>
                        </div>
                        <input
                            type="range"
                            className={cls}
                            min="0" max="1" step="0.05"
                            value={weights[key]}
                            onChange={e => setWeights({ ...weights, [key]: +e.target.value })}
                        />
                    </div>
                ))}

                <button className="cta-btn" onClick={onCompute} disabled={!canCompute}>
                    <span>{loading ? '· COMPUTING ·' : '▶ COMPUTE SAFE ROUTE'}</span>
                </button>
                <p className="shortcut-hint">{canCompute ? 'Press ⏎ Enter to compute' : 'Pin origin & destination on map'}</p>

                {error && <p className="error-text">⚠ {error}</p>}

                {/* Share button — shown once a route is computed */}
                {routes.length > 0 && onShare && (
                    <button
                        type="button"
                        className={`share-btn ${shareCopied ? 'copied' : ''}`}
                        onClick={onShare}
                        title="Copy share link"
                    >
                        {shareCopied ? '✓ Link copied!' : '⎘ Share this route'}
                    </button>
                )}
            </div>

            {/* ── Results ── */}
            {routes.length > 0 && (
                <>
                    <div className="section-header">
                        <span className="section-label">Route Analysis</span>
                        <span className="section-badge">{routes.length} paths</span>
                    </div>

                    {/* Selected route summary strip */}
                    {selectedRoute && (
                        <div className="route-summary-strip">
                            <span className="summary-item">
                                <span className="summary-icon">⏱</span>
                                {selectedRoute.cost_breakdown.travel_time_minutes.toFixed(0)} min
                            </span>
                            <span className="summary-sep" />
                            <span className="summary-item">
                                <span className="summary-icon">⇔</span>
                                {selectedRoute.cost_breakdown.distance_km.toFixed(1)} km
                            </span>
                            <span className="summary-sep" />
                            <span className="summary-item" style={{ color: selectedRoute.cost_breakdown.avg_aqi < 100 ? 'var(--acid)' : 'var(--amber)' }}>
                                <span className="summary-icon">🌫</span>
                                AQI {selectedRoute.cost_breakdown.avg_aqi.toFixed(0)}
                            </span>
                        </div>
                    )}

                    <div className="results-section">
                        {routes.map(route => {
                            const fastestTime = Math.min(
                                ...routes.map(r => r.cost_breakdown.travel_time_minutes)
                            );
                            return (
                                <RouteCard
                                    key={route.route_id}
                                    route={route}
                                    isSelected={selectedRoute?.route_id === route.route_id}
                                    onClick={() => setSelectedRoute(route)}
                                    fastestTime={fastestTime}
                                />
                            );
                        })}

                        {selectedRoute && (
                            <div className="route-detail-panel">
                                <button
                                    type="button"
                                    className="route-detail-toggle"
                                    onClick={() => setSegmentsExpanded(v => !v)}
                                >
                                    <span>Segment detail</span>
                                    <span className="route-detail-count">
                                        {selectedRoute.segments?.length || 0} segs
                                    </span>
                                    <span className={`chevron ${segmentsExpanded ? 'open' : ''}`}>›</span>
                                </button>

                                {segmentsExpanded && (
                                    <div className="segment-list">
                                        {(selectedRoute.segments || []).length > 0
                                            ? selectedRoute.segments.map((seg, i) => (
                                                <SegmentRow key={`${seg.edge_id}-${i}`} segment={seg} />
                                            ))
                                            : <p className="segment-empty">No segment data for mock routes.</p>
                                        }
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </>
            )}
        </aside>
    );
}

const AQI_LABEL = (v) =>
    v < 50 ? 'Good' : v < 100 ? 'Moderate' : v < 150 ? 'Unhealthy·SG' : 'Unhealthy';

const RouteCard = memo(function RouteCard({ route, isSelected, onClick, fastestTime }) {
    const cb = route.cost_breakdown;

    const aqiCol =
        cb.avg_aqi < 50  ? 'var(--acid)'  :
        cb.avg_aqi < 100 ? 'var(--amber)' : 'var(--infra)';

    const profileColor = {
        fastest: 'var(--ice)', safest: 'var(--acid)',
        healthiest: 'var(--amber)', balanced: 'var(--violet)',
    }[route.profile] || 'var(--text-primary)';

    const profileIcon = {
        fastest: '⚡', safest: '🛡️', healthiest: '🫁', balanced: '⚖️',
    }[route.profile] || '';

    // How many minutes longer than fastest?
    const timeDelta = fastestTime
        ? cb.travel_time_minutes - fastestTime
        : null;

    return (
        <div
            className={`result-card ${route.profile} ${isSelected ? 'selected' : ''}`}
            onClick={onClick}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && onClick()}
        >
            {/* Header row */}
            <div className="result-header">
                <span className="result-profile-name" style={{ color: profileColor }}>
                    {profileIcon} {route.profile.toUpperCase()}
                </span>
                <div className="result-header-right">
                    {cb.accident_hotspots_passed > 0 && (
                        <span className="hotspot-chip" title={`${cb.accident_hotspots_passed} blackspot(s) on route`}>
                            ⚠ {cb.accident_hotspots_passed}
                        </span>
                    )}
                    <span className="result-score">{cb.total_cost.toFixed(2)}</span>
                </div>
            </div>

            {/* Stats row */}
            <div className="result-stats">
                <div className="result-stat">
                    <span className="result-stat-value" style={{ color: 'var(--ice)' }}>
                        {cb.travel_time_minutes.toFixed(0)}
                        <span className="result-stat-unit">min</span>
                    </span>
                    <span className="result-stat-label">travel</span>
                </div>
                <div className="result-stat">
                    <span className="result-stat-value">
                        {cb.distance_km.toFixed(1)}
                        <span className="result-stat-unit">km</span>
                    </span>
                    <span className="result-stat-label">distance</span>
                </div>
                <div className="result-stat">
                    <span className="result-stat-value" style={{ color: aqiCol }}>
                        {cb.avg_aqi.toFixed(0)}
                    </span>
                    <span className="result-stat-label">avg AQI</span>
                </div>
            </div>

            {/* AQI category + time delta row */}
            <div className="result-meta-row">
                <span className="aqi-category-pill" style={{ borderColor: aqiCol, color: aqiCol }}>
                    <span className="aqi-dot-sm" style={{ background: aqiCol }} />
                    {AQI_LABEL(cb.avg_aqi)}
                </span>
                {timeDelta !== null && timeDelta > 0.5 && (
                    <span className="time-delta-badge">
                        +{timeDelta.toFixed(0)} min vs fastest
                    </span>
                )}
                {timeDelta !== null && timeDelta <= 0.5 && (
                    <span className="time-delta-badge fastest">Fastest option</span>
                )}
            </div>

            {/* AQI exposure bar */}
            <div className="aqi-bar-track">
                <div
                    className="aqi-bar-fill"
                    style={{
                        width: `${Math.min((cb.avg_aqi / 200) * 100, 100)}%`,
                        background: aqiCol,
                    }}
                />
            </div>
        </div>
    );
});

const SegmentRow = memo(function SegmentRow({ segment }) {
    const color = aqiColor(segment.aqi_value ?? 0);
    return (
        <div className="segment-row">
            <div className="segment-road">
                <span className="segment-road-name">{segment.road_name || 'Unnamed road'}</span>
                {segment.risk_score > 0.5 && (
                    <span className="risk-indicator" title="Elevated accident risk">!</span>
                )}
            </div>
            <div className="segment-meta">
                <span>{Math.round(segment.length_m || 0)} m</span>
                <span>{Math.round(segment.travel_time_s || 0)} s</span>
                <span className="segment-aqi">
                    <span className="aqi-dot" style={{ background: color }} />
                    AQI {Math.round(segment.aqi_value || 0)}
                </span>
            </div>
        </div>
    );
});
