// Renders a single route as a structured analysis report (SAFEST ROUTE /
// stats / Why this route) rather than as a chat message — this is meant
// to read like a route-planning result, matching the primary SafeMAPS
// results list (Sidebar.jsx), not like an AI chatbot reply.

const PROFILE_LABELS = {
    fastest:    'Fastest',
    safest:     'Safest',
    healthiest: 'Healthiest',
    balanced:   'Balanced',
};

// Same four route colors used throughout SafeMAPS (Sidebar.jsx / MapView.jsx / AIMap.jsx).
const PROFILE_COLORS = {
    fastest:    'var(--ice)',
    safest:     'var(--acid)',
    healthiest: 'var(--amber)',
    balanced:   'var(--violet)',
};

// Mirrors the AQI categorisation already used on the primary SafeMAPS
// results list, so both surfaces agree on what "moderate" means.
function aqiLabel(v) {
    if (v == null) return null;
    if (v < 50) return 'Good';
    if (v < 100) return 'Moderate';
    if (v < 150) return 'Unhealthy for sensitive groups';
    return 'Unhealthy';
}
function aqiColor(v) {
    if (v == null) return 'var(--text-secondary)';
    if (v < 50) return 'var(--acid)';
    if (v < 100) return 'var(--amber)';
    return 'var(--infra)';
}

export default function RouteAnalysis({ route, allRoutes = [], locations }) {
    if (!route) return null;

    const profile = route.profile || route.route_id || 'balanced';
    const label = PROFILE_LABELS[profile] || profile;
    const accent = PROFILE_COLORS[profile] || 'var(--acid)';

    // Comparisons are only drawn against routes returned in the same
    // tool result as this one (allRoutes) — never against unrelated
    // routes from earlier in the conversation. That keeps every number
    // here grounded in data SafeMAPS actually returned together.
    const comparable = allRoutes.length > 1;
    const hotspots = route.accident_hotspots_passed ?? null;
    const minHotspots = comparable
        ? Math.min(...allRoutes.map(r => r.accident_hotspots_passed ?? Infinity))
        : null;
    const isLowestRisk = comparable && hotspots != null && hotspots === minHotspots;

    const fastestTime = comparable
        ? Math.min(...allRoutes.map(r => r.travel_time_minutes ?? Infinity))
        : null;
    const delta = fastestTime != null && route.travel_time_minutes != null
        ? route.travel_time_minutes - fastestTime
        : null;

    const aqi = route.avg_aqi;

    return (
        <div className="route-analysis" style={{ borderLeftColor: accent }}>
            <div className="route-analysis-head">
                <span className="route-analysis-kicker">{label} route</span>
                {locations && (
                    <span className="route-analysis-od">
                        {locations.origin} <span className="route-analysis-arrow">→</span> {locations.destination}
                    </span>
                )}
            </div>

            <div className="route-analysis-stats">
                {route.travel_time_minutes != null && (
                    <span className="route-analysis-stat">
                        <b>{Math.round(route.travel_time_minutes)}</b> min
                    </span>
                )}
                {route.distance_km != null && (
                    <span className="route-analysis-stat">
                        <b>{route.distance_km.toFixed(1)}</b> km
                    </span>
                )}
                {aqi != null && (
                    <span className="route-analysis-stat" style={{ color: aqiColor(aqi) }}>
                        <b>{Math.round(aqi)}</b> AQI
                    </span>
                )}
            </div>

            {(hotspots != null || aqi != null || delta != null) && (
                <div className="route-analysis-why">
                    <span className="route-analysis-kicker">Why this route</span>
                    <div className="why-list">
                        {hotspots != null && (
                            <div className="why-row">
                                <span className="why-label">Accident exposure</span>
                                <span className="why-value">
                                    {hotspots === 0
                                        ? 'No modeled hotspots'
                                        : `${hotspots} modeled hotspot${hotspots === 1 ? '' : 's'}`}
                                    {isLowestRisk ? ' · lowest of the routes compared' : ''}
                                </span>
                            </div>
                        )}
                        {aqi != null && (
                            <div className="why-row">
                                <span className="why-label">Air quality</span>
                                <span className="why-value">{aqiLabel(aqi)} · avg AQI {Math.round(aqi)}</span>
                            </div>
                        )}
                        {delta != null && (
                            <div className="why-row">
                                <span className="why-label">Travel time</span>
                                <span className="why-value">
                                    {delta <= 0.5 ? 'Fastest of the routes compared' : `+${delta.toFixed(0)} min vs. fastest`}
                                </span>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
