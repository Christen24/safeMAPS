import { useState, useCallback, useRef, useEffect } from 'react';
import { aqiColor } from './MapView';

const PROFILES = [
    { id: 'fastest', label: 'Fastest', sub: 'Min. Time', icon: '⚡' },
    { id: 'safest', label: 'Safest', sub: 'Max. Security', icon: '🛡️' },
    { id: 'healthiest', label: 'Healthiest', sub: 'Low AQI', icon: '🫁' },
    { id: 'balanced', label: 'Balanced', sub: 'Weighted', icon: '⚖️' },
];

// ── Bug 7 fix: Nominatim geocoding for place name search ────────────
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
    } catch {
        return [];
    }
}

function PlaceInput({ placeholder, value, onSelect, indicator }) {
    const [query, setQuery] = useState('');
    const [suggestions, setSuggestions] = useState([]);
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [displayName, setDisplayName] = useState('');
    const debounceRef = useRef(null);
    const wrapperRef = useRef(null);

    // Close dropdown when clicking outside
    useEffect(() => {
        const handler = (e) => {
            if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
                setShowSuggestions(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    // Update display when value changes externally (e.g. map click)
    useEffect(() => {
        if (value.lat && value.lon && !displayName) {
            setDisplayName(`${(+value.lat).toFixed(4)}, ${(+value.lon).toFixed(4)}`);
        }
        if (!value.lat && !value.lon) {
            setDisplayName('');
            setQuery('');
        }
    }, [value.lat, value.lon]);

    const handleInputChange = useCallback((e) => {
        const q = e.target.value;
        setQuery(q);
        setDisplayName('');

        if (debounceRef.current) clearTimeout(debounceRef.current);

        if (q.length >= 3) {
            debounceRef.current = setTimeout(async () => {
                const results = await geocode(q);
                setSuggestions(results);
                setShowSuggestions(results.length > 0);
            }, 300);
        } else {
            setSuggestions([]);
            setShowSuggestions(false);
        }
    }, []);

    const handleSelect = useCallback((item) => {
        const shortName = item.display_name.split(',').slice(0, 2).join(', ');
        setDisplayName(shortName);
        setQuery('');
        setSuggestions([]);
        setShowSuggestions(false);
        onSelect({ lat: item.lat, lon: item.lon });
    }, [onSelect]);

    return (
        <div className="place-input-wrapper" ref={wrapperRef}>
            <div className="input-group">
                <span className={`indicator ${indicator}`} />
                <input
                    placeholder={placeholder}
                    value={displayName || query}
                    onChange={handleInputChange}
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

    useEffect(() => {
        setSegmentsExpanded(false);
    }, [selectedRoute?.route_id]);

    return (
        <aside className="sidebar">
            {/* Route Input */}
            <div className="route-input-section">
                <div className="section-label">Route</div>

                <PlaceInput
                    placeholder="🔍 Search origin (e.g. Indiranagar)"
                    value={origin}
                    onSelect={setOrigin}
                    indicator="origin"
                />

                <button className="swap-btn" onClick={onSwap} title="Swap origin & destination">⇅</button>

                <PlaceInput
                    placeholder="🔍 Search destination (e.g. Koramangala)"
                    value={destination}
                    onSelect={setDestination}
                    indicator="dest"
                />

                <div className="section-label" style={{ marginTop: 16 }}>Departure Time</div>
                <input
                    type="datetime-local"
                    className="departure-input"
                    value={departureTime || ''}
                    onChange={(e) => setDepartureTime(e.target.value || null)}
                />

                {/* Route Profile Grid */}
                <div className="section-label" style={{ marginTop: 16 }}>Route Profile</div>
                <div className="profile-grid">
                    {PROFILES.map(p => (
                        <div
                            key={p.id}
                            className={`profile-card ${p.id} ${profile === p.id ? 'active' : ''}`}
                            onClick={() => setProfile(p.id)}
                        >
                            <div className="profile-icon">{p.icon}</div>
                            <div className="profile-name">{p.label}</div>
                            <div className="profile-sub">{p.sub}</div>
                        </div>
                    ))}
                </div>

                <p className="hint">💡 Click on the map or search by place name</p>
            </div>

            {/* Cost Weights */}
            <div className="weights-section">
                <div className="section-label">Cost Weights (α, β, γ)</div>

                <div className="weight-row">
                    <div className="weight-header">
                        <span className="weight-label">⏱️ Travel Time (α)</span>
                        <span className="weight-value time">{weights.alpha.toFixed(2)}</span>
                    </div>
                    <input type="range" className="time" min="0" max="1" step="0.05"
                        value={weights.alpha}
                        onChange={e => setWeights({ ...weights, alpha: +e.target.value })} />
                </div>

                <div className="weight-row">
                    <div className="weight-header">
                        <span className="weight-label">🌫️ AQI Exposure (β)</span>
                        <span className="weight-value aqi">{weights.beta.toFixed(2)}</span>
                    </div>
                    <input type="range" className="aqi" min="0" max="1" step="0.05"
                        value={weights.beta}
                        onChange={e => setWeights({ ...weights, beta: +e.target.value })} />
                </div>

                <div className="weight-row">
                    <div className="weight-header">
                        <span className="weight-label">⚠️ Accident Risk (γ)</span>
                        <span className="weight-value risk">{weights.gamma.toFixed(2)}</span>
                    </div>
                    <input type="range" className="risk" min="0" max="1" step="0.05"
                        value={weights.gamma}
                        onChange={e => setWeights({ ...weights, gamma: +e.target.value })} />
                </div>

                <button className="cta-btn" onClick={onCompute} disabled={!canCompute}>
                    {loading ? '⏳ Computing...' : '🔍 Compute Safe Route'}
                </button>
                {error && <p className="error-text">{error}</p>}
            </div>

            {/* Route Results */}
            {routes.length > 0 && (
                <div className="results-section">
                    <div className="section-label">Route Comparison · {routes.length} alternative{routes.length > 1 ? 's' : ''}</div>
                    {routes.map(route => (
                        <RouteCard key={route.route_id} route={route}
                            isSelected={selectedRoute?.route_id === route.route_id}
                            onClick={() => setSelectedRoute(route)} />
                    ))}

                    {selectedRoute && (
                        <div className="route-detail-panel">
                            <button
                                type="button"
                                className="route-detail-toggle"
                                onClick={() => setSegmentsExpanded((open) => !open)}
                            >
                                <span>Route segments</span>
                                <span className="route-detail-count">{selectedRoute.segments?.length || 0}</span>
                                <span className={`chevron ${segmentsExpanded ? 'open' : ''}`}>›</span>
                            </button>

                            {segmentsExpanded && (
                                <div className="segment-list">
                                    {(selectedRoute.segments || []).length > 0 ? (
                                        selectedRoute.segments.map((segment, index) => (
                                            <SegmentRow key={`${segment.edge_id}-${index}`} segment={segment} />
                                        ))
                                    ) : (
                                        <p className="segment-empty">Segment details unavailable for mock routes.</p>
                                    )}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}
        </aside>
    );
}

function RouteCard({ route, isSelected, onClick }) {
    const cb = route.cost_breakdown;
    const avgAqiColor = cb.avg_aqi < 50 ? 'var(--primary)' : cb.avg_aqi < 100 ? 'var(--accent-amber)' : 'var(--error)';

    return (
        <div className={`result-card ${route.profile} ${isSelected ? 'selected' : ''}`} onClick={onClick}>
            <div className="result-header">
                <span className="result-profile-name" style={{ color: getProfileColor(route.profile) }}>
                    {getProfileIcon(route.profile)} {route.profile}
                </span>
                <span className="result-score">{cb.total_cost.toFixed(1)} cost</span>
            </div>
            <div className="result-stats">
                <div className="result-stat">
                    <div className="result-stat-value" style={{ color: 'var(--secondary)' }}>{cb.travel_time_minutes.toFixed(0)}m</div>
                    <div className="result-stat-label">Time</div>
                </div>
                <div className="result-stat">
                    <div className="result-stat-value">{cb.distance_km.toFixed(1)}km</div>
                    <div className="result-stat-label">Distance</div>
                </div>
                <div className="result-stat">
                    <div className="result-stat-value" style={{ color: avgAqiColor }}>{cb.avg_aqi.toFixed(0)}</div>
                    <div className="result-stat-label">Avg AQI</div>
                </div>
                <div className="result-stat">
                    <div className="result-stat-value" style={{ color: cb.accident_hotspots_passed > 0 ? 'var(--error)' : 'var(--primary)' }}>
                        {cb.accident_hotspots_passed}
                    </div>
                    <div className="result-stat-label">Hotspots</div>
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
                {segment.risk_score > 0.5 && <span className="risk-indicator" title="Elevated accident risk">!</span>}
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

function getProfileColor(p) {
    return { fastest: 'var(--secondary)', safest: 'var(--primary)', healthiest: 'var(--accent-amber)', balanced: 'var(--tertiary)' }[p] || 'var(--on-surface)';
}

function getProfileIcon(p) {
    return { fastest: '⚡', safest: '🛡️', healthiest: '🫁', balanced: '⚖️' }[p] || '';
}
