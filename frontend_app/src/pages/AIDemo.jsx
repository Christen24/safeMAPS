import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AIMap from '../components/ai/AIMap';
import MarkdownMessage from '../components/ai/MarkdownMessage';
import ToolCallCard from '../components/ai/ToolCallCard';
import { getAIStatus, streamAIChat } from '../services/chatApi';

const PROMPTS = [
    'Safest route from Koramangala to Whitefield',
    'Compare safest vs fastest route from Indiranagar to Electronic City',
    "What's the AQI around MG Road?",
    'Show accident risk near Hebbal',
];

// ── Routing profiles definition ───────────────────────────────
const PROFILES = [
    { id: 'fastest',    icon: '⚡', label: 'Fastest',    sub: 'Min. Time',     color: '#4fc3e0' },
    { id: 'safest',     icon: '🛡️', label: 'Safest',     sub: 'Max. Security', color: '#4ecb8d' },
    { id: 'healthiest', icon: '🫁', label: 'Healthiest', sub: 'Low AQI',       color: '#f0a93e' },
    { id: 'balanced',   icon: '⚖️', label: 'Balanced',   sub: 'Weighted',      color: '#9b87e8' },
];

// Extract all routes with geometry from completed tool events
function extractRoutes(toolEvents) {
    const routes = [];
    for (const event of toolEvents) {
        const result = event.result;
        if (!result || event.status !== 'completed') continue;
        if (result.geometry) routes.push(result);
        if (Array.isArray(result.routes)) routes.push(...result.routes.filter(r => r.geometry));
    }
    return routes;
}

// Extract the most recent route context (origin/dest) from tool events
// to enable profile switching without re-asking the user
function extractRouteContext(toolEvents) {
    for (let i = toolEvents.length - 1; i >= 0; i--) {
        const e = toolEvents[i];
        if (e.status === 'completed' && (e.tool === 'get_safe_route' || e.tool === 'compare_route_profiles')) {
            const args = e.arguments || {};
            if (args.origin_lat && args.dest_lat) {
                return {
                    origin_lat: args.origin_lat,
                    origin_lon: args.origin_lon,
                    dest_lat: args.dest_lat,
                    dest_lon: args.dest_lon,
                };
            }
        }
    }
    return null;
}

// Which profiles are present in returned route data
function getActiveProfiles(routes) {
    return new Set(routes.map(r => r.profile || r.route_id).filter(Boolean));
}

// Get route data for a specific profile
function getRouteForProfile(routes, profileId) {
    return routes.find(r => (r.profile || r.route_id) === profileId) || null;
}

// Format a route object into structured display fields
function formatRouteMetrics(route) {
    if (!route) return null;
    return {
        distance_km:              route.distance_km,
        travel_time_minutes:      route.travel_time_minutes,
        avg_aqi:                  route.avg_aqi,
        max_aqi:                  route.max_aqi,
        accident_hotspots_passed: route.accident_hotspots_passed,
    };
}

// Structured route result panel — no raw Markdown, no UUIDs
function RouteResultPanel({ route, profileId }) {
    if (!route) return null;
    const prof   = PROFILES.find(p => p.id === profileId);
    const color  = prof?.color || '#4ecb8d';
    const label  = prof?.label || profileId;
    const icon   = prof?.icon  || '';

    const dist = route.distance_km != null ? `${route.distance_km} km` : null;
    const time = route.travel_time_minutes != null ? `${Math.round(route.travel_time_minutes)} min` : null;

    return (
        <div className="ai-route-result">
            <div className="ai-route-result-header" style={{ '--rr-color': color }}>
                <span className="ai-route-result-icon">{icon}</span>
                <span className="ai-route-result-label">{label} Route</span>
            </div>

            {(dist || time) && (
                <div className="ai-route-result-summary">
                    {dist && <span>{dist}</span>}
                    {time && <span>{time}</span>}
                </div>
            )}

            <div className="ai-route-result-divider" />

            <div className="ai-route-result-metrics">
                {route.avg_aqi != null && (
                    <div className="ai-route-metric">
                        <span className="ai-route-metric-label">Air Quality</span>
                        <div className="ai-route-metric-row">
                            <span>Average AQI</span>
                            <span className="ai-route-metric-val">{route.avg_aqi}</span>
                        </div>
                        {route.max_aqi != null && (
                            <div className="ai-route-metric-row">
                                <span>Peak AQI</span>
                                <span className="ai-route-metric-val">{route.max_aqi}</span>
                            </div>
                        )}
                    </div>
                )}
                {route.accident_hotspots_passed != null && (
                    <div className="ai-route-metric">
                        <span className="ai-route-metric-label">Safety</span>
                        <div className="ai-route-metric-row">
                            <span>Accident hotspots</span>
                            <span className="ai-route-metric-val">{route.accident_hotspots_passed}</span>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

export default function AIDemo({ onBack }) {
    const [messages, setMessages]         = useState([]);
    const [input, setInput]               = useState('');
    const [toolEvents, setToolEvents]     = useState([]);
    const [status, setStatus]             = useState({ mcp: 'connecting', tool_count: 0, tools: [] });
    const [sessionId, setSessionId]       = useState(() => crypto.randomUUID());
    const [loading, setLoading]           = useState(false);
    const [selectedProfile, setSelectedProfile] = useState(null); // which profile tab is active
    const abortRef = useRef(null);

    const routes         = useMemo(() => extractRoutes(toolEvents),     [toolEvents]);
    const routeCtx       = useMemo(() => extractRouteContext(toolEvents), [toolEvents]);
    const activeProfiles = useMemo(() => getActiveProfiles(routes),     [routes]);

    // Auto-select the first profile returned when new routes arrive
    useEffect(() => {
        if (routes.length && !selectedProfile) {
            const first = routes[0].profile || routes[0].route_id;
            if (first) setSelectedProfile(first);
        }
    }, [routes, selectedProfile]);

    // The route object for the currently selected profile
    const selectedRoute = useMemo(
        () => getRouteForProfile(routes, selectedProfile),
        [routes, selectedProfile]
    );

    useEffect(() => {
        let cancelled = false;
        getAIStatus()
            .then(data => { if (!cancelled) setStatus(data); })
            .catch(err  => { if (!cancelled) setStatus({ mcp: 'unavailable', tool_count: 0, tools: [], error: err.message }); });
        return () => { cancelled = true; };
    }, []);

    const handleEvent = useCallback(event => {
        if (event.session_id) setSessionId(event.session_id);

        if (event.type === 'mcp_status') {
            setStatus(prev => ({ ...prev, mcp: event.status, tool_count: event.tool_count }));
        }
        if (event.type === 'tool_start') {
            const id = `${event.tool}-${Date.now()}-${Math.random()}`;
            setToolEvents(prev => [...prev, { id, status: 'running', tool: event.tool, arguments: event.arguments }]);
        }
        if (event.type === 'tool_result') {
            setToolEvents(prev => {
                const next  = [...prev];
                const index = [...next].reverse().findIndex(item =>
                    item.tool === event.tool && item.status === 'running'
                );
                if (index >= 0) {
                    const realIndex = next.length - 1 - index;
                    next[realIndex] = {
                        ...next[realIndex],
                        status:      event.result?.error ? 'failed' : 'completed',
                        duration_ms: event.duration_ms,
                        result:      event.result,
                    };
                }
                return next;
            });
        }
        if (event.type === 'text_delta') {
            setMessages(prev => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.role === 'assistant' && last.streaming) {
                    last.content += event.content;
                } else {
                    next.push({ role: 'assistant', content: event.content, streaming: true });
                }
                return next;
            });
        }
        if (event.type === 'error') {
            setMessages(prev => [...prev, { role: 'assistant', content: event.message || 'SafeMAPS AI failed.' }]);
        }
        if (event.type === 'done') {
            setLoading(false);
            setMessages(prev => prev.map(m => ({ ...m, streaming: false })));
        }
    }, []);

    // Submit a user message to the AI
    const submit = useCallback(async prompt => {
        const message = (prompt || input).trim();
        if (!message || loading) return;
        setInput('');
        setLoading(true);
        setMessages(prev => [...prev, { role: 'user', content: message }]);
        abortRef.current?.abort();
        abortRef.current = new AbortController();
        try {
            await streamAIChat({ message, sessionId, signal: abortRef.current.signal, onEvent: handleEvent });
        } catch (err) {
            setLoading(false);
            setMessages(prev => [...prev, { role: 'assistant', content: err.message }]);
        }
    }, [input, loading, sessionId, handleEvent]);

    // Handle clicking a profile tab
    // Case A: route already exists in current data → just select it
    // Case B: route not loaded → trigger a real AI/MCP request for that profile
    const handleProfileClick = useCallback(async (profileId) => {
        setSelectedProfile(profileId);

        const alreadyLoaded = activeProfiles.has(profileId);
        if (alreadyLoaded) return; // Case A — just switch view

        // Case B — need to fetch this profile
        if (!routeCtx) {
            // No route context yet; ask user to ask for a route first
            return;
        }

        // Build a natural-language request so the AI uses the same MCP pipeline
        const originName = `origin (${routeCtx.origin_lat.toFixed(4)}, ${routeCtx.origin_lon.toFixed(4)})`;
        const destName   = `destination (${routeCtx.dest_lat.toFixed(4)}, ${routeCtx.dest_lon.toFixed(4)})`;
        const profileMsg = `Show me the ${profileId} route between the same ${originName} and ${destName}`;

        setLoading(true);
        setMessages(prev => [...prev, { role: 'user', content: `Switching to ${profileId} route…`, _synthetic: true }]);
        abortRef.current?.abort();
        abortRef.current = new AbortController();
        try {
            await streamAIChat({ message: profileMsg, sessionId, signal: abortRef.current.signal, onEvent: handleEvent });
        } catch (err) {
            setLoading(false);
            setMessages(prev => [...prev, { role: 'assistant', content: err.message }]);
        }
    }, [activeProfiles, routeCtx, sessionId, handleEvent]);

    return (
        <div className="ai-demo-shell">
            <header className="ai-demo-header">
                <div className="ai-demo-brand">
                    <button className="ai-back-btn" onClick={onBack} aria-label="Back to SafeMAPS">
                        Back
                    </button>
                    <div>
                        <h1>SafeMAPS AI</h1>
                        <p>AI-powered safer routing</p>
                    </div>
                </div>
                {/* MCP status intentionally NOT shown in the main UI */}
            </header>

            <main className="ai-demo-main">
                {/* ── Chat panel ── */}
                <section className="ai-chat-panel">
                    <div className="ai-chat-scroll">
                        {!messages.length && (
                            <div className="ai-empty-state">
                                <h2>Ask SafeMAPS about routes, air quality, and accident risk.</h2>
                                <div className="ai-suggested">
                                    {PROMPTS.map(prompt => (
                                        <button key={prompt} onClick={() => submit(prompt)}>
                                            {prompt}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        {messages.map((message, index) => {
                            // Suppress synthetic profile-switch messages from the chat log
                            if (message._synthetic) return null;
                            return (
                                <div key={index} className={`ai-message ${message.role}`}>
                                    <span>{message.role === 'user' ? 'You' : 'SafeMAPS'}</span>
                                    {message.role === 'assistant'
                                        ? <MarkdownMessage content={message.content} />
                                        : <p>{message.content}</p>
                                    }
                                </div>
                            );
                        })}

                        {toolEvents.map(event => (
                            <ToolCallCard key={event.id} event={event} />
                        ))}

                        {loading && <div className="ai-progress">Routing…</div>}

                        {/* Structured route result for selected profile */}
                        {selectedRoute && (
                            <RouteResultPanel route={selectedRoute} profileId={selectedProfile} />
                        )}
                    </div>

                    <form className="ai-input-bar" onSubmit={e => { e.preventDefault(); submit(); }}>
                        <input
                            value={input}
                            onChange={e => setInput(e.target.value)}
                            placeholder="Ask SafeMAPS…"
                            maxLength={1200}
                        />
                        <button type="submit" disabled={loading || !input.trim()}>Send</button>
                    </form>
                </section>

                {/* ── Map panel ── */}
                <section className="ai-map-panel">
                    {/* Route profile strip */}
                    <div className="ai-profile-strip">
                        {PROFILES.map(p => {
                            const isLoaded    = activeProfiles.has(p.id);
                            const isSelected  = selectedProfile === p.id;
                            return (
                                <button
                                    key={p.id}
                                    className={`ai-profile-card ${isSelected ? 'active' : ''} ${isLoaded ? 'loaded' : ''}`}
                                    style={{ '--prof-color': p.color }}
                                    onClick={() => handleProfileClick(p.id)}
                                    title={isLoaded ? `Switch to ${p.label} route` : `Request ${p.label} route`}
                                >
                                    <span className="ai-profile-icon">{p.icon}</span>
                                    <span className="ai-profile-label">{p.label}</span>
                                    <span className="ai-profile-sub">{p.sub}</span>
                                    {isLoaded && <span className="ai-profile-dot" />}
                                </button>
                            );
                        })}
                    </div>

                    {/* Map */}
                    <div className="ai-map-toolbar">
                        <div>
                            <span>Route Visualization</span>
                            <b>{routes.length ? `${routes.length} route${routes.length === 1 ? '' : 's'}` : 'Waiting for result'}</b>
                        </div>
                    </div>

                    <AIMap routes={routes} selectedProfile={selectedProfile} />
                </section>
            </main>
        </div>
    );
}
