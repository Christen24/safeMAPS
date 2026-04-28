import { useState, useCallback, useRef, useEffect } from 'react';
import { aqiColor } from './MapView';

const PROFILES = [
    { id: 'fastest',    label: 'Fastest',    sub: 'Min. Time',      icon: '⚡', color: 'var(--ice)'    },
    { id: 'safest',     label: 'Safest',     sub: 'Max. Security',  icon: '🛡️', color: 'var(--acid)'   },
    { id: 'healthiest', label: 'Healthiest', sub: 'Low AQI',        icon: '🫁', color: 'var(--amber)'  },
    { id: 'balanced',   label: 'Balanced',   sub: 'Weighted',       icon: '⚖️', color: 'var(--violet)' },
];

async function geocode(query) {
    if (!query || query.length < 3) return [];
    try {
        const resp = await fetch(
            `https://nominatim.openstreetmap.org/search?` +
            `q=${encodeURIComponent(query + ', Bangalore')}&format=json&limit=5`,
            { headers: { 'Accept-Language': 'en' } }
        );
        if (!resp.ok) return [];
        const results = await resp.json();
        return results.map(({ lat, lon, display_name }) => ({ lat, lon, display_name }));
    } catch { return []; }
}

function PlaceInput({ placeholder, value, onSelect, indicator }) {
    const [query, setQuery]               = useState('');
    const [suggestions, setSuggestions]   = useState([]);
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [displayName, setDisplayName]   = useState('');
    const debounceRef = useRef(null);
    const wrapperRef  = useRef(null);

    useEffect(() => {
        const h = (e) => {
            if (wrapperRef.current && !wrapperRef.current.contains(e.target))
                setShowSuggestions(false);
        };
        document.addEventListener('mousedown', h);
        return () => document.removeEventListener('mousedown', h);
    }, []);

    useEffect(() => {
        if (value.lat && value.lon && !displayName)
            setDisplayName(`${(+value.lat).toFixed(4)}, ${(+value.lon).toFixed(4)}`);
        if (!value.lat && !value.lon) { setDisplayName(''); setQuery(''); }
    }, [value.lat, value.lon]);

    const handleChange = useCallback((e) => {
        const q = e.target.value;
        setQuery(q);
        setDisplayName('');
        if (debounceRef.current) clearTimeout(debounceRef.current);
        if (q.length >= 3) {
            debounceRef.current = setTimeout(async () => {
                const res = await geocode(q);
                setSuggestions(res);
                setShowSuggestions(res.length > 0);
            }, 300);
        } else { setSuggestions([]); setShowSuggestions(false); }
    }, []);

    const handleSelect = useCallback((item) => {
        const short = item.display_name.split(',').slice(0, 2).join(', ');
        setDisplayName(short);
        setQuery('');
        setSuggestions([]);
        setShowSuggestions(false);
        onSelect({ lat: item.lat, lon: item.lon });
    }, [onSelect]);

    return (
        <div className="place-input-wrapper" ref={wrapperRef}>
            <div className="input-row">
                <div className="input-pip">
                    <span className={`pip-dot ${indicator}`} />
                </div>
                <input
                    placeholder={placeholder}
                    value={displayName || query}
                    onChange={handleChange}
                    onFocus={() => { if (suggestions.length) setShowSuggestions(true); }}
                />
            </div>
            {showSuggestions && (
                <ul className="geocode-dropdown">
                    {suggestions.map((s, i) => (
                        <li className="geocode-option" key={i} onClick={() => handleSelect(s)}>
                            <span className="suggestion-name">
                                {s.display_name.split(',').slice(0, 2).join(', ')}
                            </span>
                            <span className="suggestion-detail">
                                {s.display_name.split(',').slice(2, 4).join(', ')}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

export default function Sidebar({
    origin, destination, setOrigin, setDestination,
    profile, setProfile,
    weights, setWeights,
    departureTime, setDepartureTime,
    routes, selectedRoute, setSelectedRoute,
    onCompute, onSwap, loading, error,
}) {
    const canCompute = origin.lat && origin.lon && destination.lat && destination.lon && !loading;
    const [segmentsExpanded, setSegmentsExpanded] = useState(false);

    useEffect(() => { setSegmentsExpanded(false); }, [selectedRoute?.route_id]);

    return (
        <aside className="sidebar">

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

                {error && <p className="error-text">⚠ {error}</p>}
            </div>

            {/* ── Results ── */}
            {routes.length > 0 && (
                <>
                    <div className="section-header">
                        <span className="section-label">Route Analysis</span>
                        <span className="section-badge">{routes.length} paths</span>
                    </div>

                    <div className="results-section">
                        {routes.map(route => (
                            <RouteCard
                                key={route.route_id}
                                route={route}
                                isSelected={selectedRoute?.route_id === route.route_id}
                                onClick={() => setSelectedRoute(route)}
                            />
                        ))}

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

function RouteCard({ route, isSelected, onClick }) {
    const cb = route.cost_breakdown;
    const aqiCol = cb.avg_aqi < 50 ? 'var(--acid)' : cb.avg_aqi < 100 ? 'var(--amber)' : 'var(--infra)';
    const profileColor = {
        fastest: 'var(--ice)', safest: 'var(--acid)',
        healthiest: 'var(--amber)', balanced: 'var(--violet)',
    }[route.profile] || 'var(--text-primary)';
    const profileIcon = { fastest: '⚡', safest: '🛡️', healthiest: '🫁', balanced: '⚖️' }[route.profile] || '';

    return (
        <div
            className={`result-card ${route.profile} ${isSelected ? 'selected' : ''}`}
            onClick={onClick}
        >
            <div className="result-header">
                <span className="result-profile-name" style={{ color: profileColor }}>
                    {profileIcon} {route.profile.toUpperCase()}
                </span>
                <span className="result-score">{cb.total_cost.toFixed(2)}</span>
            </div>

            <div className="result-stats">
                <div className="result-stat">
                    <span className="result-stat-value" style={{ color: 'var(--ice)' }}>
                        {cb.travel_time_minutes.toFixed(0)}m
                    </span>
                    <span className="result-stat-label">time</span>
                </div>
                <div className="result-stat">
                    <span className="result-stat-value">{cb.distance_km.toFixed(1)}</span>
                    <span className="result-stat-label">km</span>
                </div>
                <div className="result-stat">
                    <span className="result-stat-value" style={{ color: aqiCol }}>
                        {cb.avg_aqi.toFixed(0)}
                    </span>
                    <span className="result-stat-label">AQI</span>
                </div>
                <div className="result-stat">
                    <span
                        className="result-stat-value"
                        style={{ color: cb.accident_hotspots_passed > 0 ? 'var(--infra)' : 'var(--acid)' }}
                    >
                        {cb.accident_hotspots_passed}
                    </span>
                    <span className="result-stat-label">spots</span>
                </div>
            </div>
        </div>
    );
}

function SegmentRow({ segment }) {
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
}
