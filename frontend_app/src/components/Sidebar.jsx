const PROFILES = [
    { id: 'fastest', label: 'Fastest', sub: 'Min. Time', icon: '⚡' },
    { id: 'safest', label: 'Safest', sub: 'Max. Security', icon: '🛡️' },
    { id: 'healthiest', label: 'Healthiest', sub: 'Low AQI', icon: '🫁' },
    { id: 'balanced', label: 'Balanced', sub: 'Weighted', icon: '⚖️' },
];

export default function Sidebar({
    origin, destination, setOrigin, setDestination,
    profile, setProfile,
    weights, setWeights,
    routes, selectedRoute, setSelectedRoute,
    onCompute, onSwap, loading, error,
}) {
    const canCompute = origin.lat && origin.lon && destination.lat && destination.lon && !loading;

    return (
        <aside className="sidebar">
            {/* Route Input */}
            <div className="route-input-section">
                <div className="section-label">Route</div>

                <div className="input-group">
                    <span className="indicator origin" />
                    <input placeholder="Origin latitude" value={origin.lat}
                        onChange={e => setOrigin({ ...origin, lat: e.target.value })} />
                </div>
                <div className="input-group">
                    <span className="indicator origin" style={{ opacity: 0.4 }} />
                    <input placeholder="Origin longitude" value={origin.lon}
                        onChange={e => setOrigin({ ...origin, lon: e.target.value })} />
                </div>

                <button className="swap-btn" onClick={onSwap} title="Swap origin & destination">⇅</button>

                <div className="input-group">
                    <span className="indicator dest" />
                    <input placeholder="Destination latitude" value={destination.lat}
                        onChange={e => setDestination({ ...destination, lat: e.target.value })} />
                </div>
                <div className="input-group">
                    <span className="indicator dest" style={{ opacity: 0.4 }} />
                    <input placeholder="Destination longitude" value={destination.lon}
                        onChange={e => setDestination({ ...destination, lon: e.target.value })} />
                </div>

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

                <p className="hint">💡 Click on the map to set origin & destination</p>
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
                    <div className="section-label">Route Comparison · {routes.length} alternatives</div>
                    {routes.map(route => (
                        <RouteCard key={route.route_id} route={route}
                            isSelected={selectedRoute?.route_id === route.route_id}
                            onClick={() => setSelectedRoute(route)} />
                    ))}
                </div>
            )}
        </aside>
    );
}

function RouteCard({ route, isSelected, onClick }) {
    const cb = route.cost_breakdown;
    const aqiColor = cb.avg_aqi < 50 ? 'var(--primary)' : cb.avg_aqi < 100 ? 'var(--accent-amber)' : 'var(--error)';

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
                    <div className="result-stat-value" style={{ color: aqiColor }}>{cb.avg_aqi.toFixed(0)}</div>
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

function getProfileColor(p) {
    return { fastest: 'var(--secondary)', safest: 'var(--primary)', healthiest: 'var(--accent-amber)', balanced: 'var(--tertiary)' }[p] || 'var(--on-surface)';
}

function getProfileIcon(p) {
    return { fastest: '⚡', safest: '🛡️', healthiest: '🫁', balanced: '⚖️' }[p] || '';
}
