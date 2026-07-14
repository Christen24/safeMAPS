import { useState, useCallback, useRef, useEffect, memo } from 'react';
import { aqiColor } from './MapView';
import SavedCommutesPanel from './SavedCommutesPanel';

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

function formatSuggestion(display_name) {
    const parts = display_name.split(',').map(p => p.trim());
    return {
        name:   parts.slice(0, 3).join(', '),
        detail: parts.slice(3, 5).join(', '),
    };
}

const PlaceInput = memo(function PlaceInput({ placeholder, value, onSelect, indicator }) {
    const [query, setQuery]               = useState('');
    const [suggestions, setSuggestions]   = useState([]);
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [displayName, setDisplayName]   = useState('');
    const debounceRef = useRef(null);
    const wrapperRef  = useRef(null);
    const requestIdRef = useRef(0);

    useEffect(() => {
        const h = (e) => {
            if (wrapperRef.current && !wrapperRef.current.contains(e.target))
                setShowSuggestions(false);
        };
        document.addEventListener('mousedown', h);
        return () => document.removeEventListener('mousedown', h);
    }, []);

    const prevCoordRef = useRef('');

    useEffect(() => {
        const coordKey = `${value.lat},${value.lon}`;
        if (value.lat && value.lon && coordKey !== prevCoordRef.current) {
            prevCoordRef.current = coordKey;
            setDisplayName(d => d && d.includes(',') && d.includes('.') ? d :
                `${(+value.lat).toFixed(4)}, ${(+value.lon).toFixed(4)}`);
        }
        if (!value.lat && !value.lon) {
            prevCoordRef.current = '';
            setDisplayName(''); setQuery('');
        }
    }, [value.lat, value.lon]);

    const handleChange = useCallback((e) => {
        const q = e.target.value;
        setQuery(q);
        setDisplayName('');
        if (debounceRef.current) clearTimeout(debounceRef.current);
        if (q.length >= 3) {
            const myRequestId = ++requestIdRef.current;
            debounceRef.current = setTimeout(async () => {
                const res = await geocode(q);
                if (myRequestId !== requestIdRef.current) return; // stale — discard
                setSuggestions(res);
                setShowSuggestions(res.length > 0);
            }, 300);
        } else {
            requestIdRef.current++;
            setSuggestions([]); setShowSuggestions(false);
        }
    }, []);

    const handleSelect = useCallback((item) => {
        const fmt = formatSuggestion(item.display_name);
        setDisplayName(fmt.name);
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
                    {suggestions.map((s, i) => {
                        const fmt = formatSuggestion(s.display_name);
                        return (
                            <li className="geocode-option" key={i} onClick={() => handleSelect(s)}>
                                <span className="suggestion-name">{fmt.name}</span>
                                <span className="suggestion-detail">{fmt.detail}</span>
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
