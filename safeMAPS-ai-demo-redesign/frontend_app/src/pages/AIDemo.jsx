import { useEffect, useMemo, useRef, useState } from 'react';
import AIMap from '../components/ai/AIMap';
import RouteAnalysis from '../components/ai/RouteAnalysis';
import ToolCallCard from '../components/ai/ToolCallCard';
import { getAIStatus, streamAIChat } from '../services/chatApi';

const PROMPTS = [
    'Safest route from Koramangala to Whitefield',
    'Compare safest vs fastest route from Indiranagar to Electronic City',
    "What's the AQI around MG Road?",
    'Show accident risk near Hebbal',
];

// ── Routing profiles — mirrors the primary SafeMAPS page ───────────
// Same four profiles and same route colors as Sidebar.jsx / MapView.jsx /
// AIMap.jsx. Icons are intentionally not carried over to this selector —
// it leans on typography, weight and color rather than iconography.
const PROFILES = [
    { id: 'fastest',    label: 'Fastest',    color: 'var(--ice)'    },
    { id: 'safest',     label: 'Safest',     color: 'var(--acid)'   },
    { id: 'healthiest', label: 'Healthiest', color: 'var(--amber)'  },
    { id: 'balanced',   label: 'Balanced',   color: 'var(--violet)' },
];

// Each completed route-bearing tool call becomes one "batch" — either a
// single route (get_safe_route) or several (compare_route_profiles).
// Keeping batches separate lets the analysis panel compare a route only
// against routes that were actually computed together, rather than
// against unrelated routes from earlier in the conversation.
function extractRouteBatches(toolEvents) {
    const batches = [];
    for (const event of toolEvents) {
        const result = event.result;
        if (!result || event.status !== 'completed') continue;
        if (result.geometry) batches.push([result]);
        if (Array.isArray(result.routes)) {
            const withGeometry = result.routes.filter(r => r.geometry);
            if (withGeometry.length) batches.push(withGeometry);
        }
    }
    return batches;
}

// Most recent route returned for each profile — later entries win, so
// the strip always reflects the latest computation for that profile.
function routesByProfile(routes) {
    const map = {};
    for (const route of routes) {
        const id = route.profile || route.route_id;
        if (id) map[id] = route;
    }
    return map;
}

// Find the most recently mentioned profile from AI messages/tool calls,
// used to decide which route the analysis panel should feature.
function inferHighlightedProfile(messages, toolEvents) {
    const text = [
        ...messages.map(m => m.content),
        ...toolEvents.map(e => e.tool),
    ].join(' ').toLowerCase();

    if (text.includes('healthiest')) return 'healthiest';
    if (text.includes('safest'))     return 'safest';
    if (text.includes('fastest'))    return 'fastest';
    if (text.includes('balanced'))   return 'balanced';
    return null;
}

// Lightweight "from X to Y" extraction from the user's own words, used
// only to label the analysis panel — never to invent route data.
function titleCaseWord(word) {
    if (/[a-z]/.test(word) && /[A-Z]/.test(word)) return word; // e.g. "MG" — leave as typed
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
}
function titleCase(phrase) {
    return phrase.split(' ').filter(Boolean).map(titleCaseWord).join(' ');
}
const LOCATION_PATTERNS = [
    /from\s+([a-z][a-z0-9 .'-]*?)\s+to\s+([a-z][a-z0-9 .'-]*?)(?:[.,!?]|\s+route\b|\s*$)/i,
    /^([a-z][a-z0-9 .'-]*?)\s+to\s+([a-z][a-z0-9 .'-]*?)(?:[.,!?]|\s+route\b|\s*$)/i,
];
function extractLocations(messages) {
    const userTexts = messages.filter(m => m.role === 'user').map(m => m.content).reverse();
    for (const text of userTexts) {
        for (const pattern of LOCATION_PATTERNS) {
            const match = text.match(pattern);
            if (match) {
                return { origin: titleCase(match[1].trim()), destination: titleCase(match[2].trim()) };
            }
        }
    }
    return null;
}

export default function AIDemo({ onBack }) {
    const [messages, setMessages]           = useState([]);
    const [input, setInput]                 = useState('');
    const [toolEvents, setToolEvents]       = useState([]);
    const [status, setStatus]               = useState({ mcp: 'connecting', tool_count: 0, tools: [] });
    const [sessionId, setSessionId]         = useState(() => crypto.randomUUID());
    const [loading, setLoading]             = useState(false);
    const [manualProfile, setManualProfile] = useState(null);
    const abortRef                          = useRef(null);
    const scrollRef                         = useRef(null);

    const routeBatches    = useMemo(() => extractRouteBatches(toolEvents), [toolEvents]);
    const routes          = useMemo(() => routeBatches.flat(), [routeBatches]);
    const profileRoutes   = useMemo(() => routesByProfile(routes), [routes]);
    const highlightedProf = useMemo(() => inferHighlightedProfile(messages, toolEvents), [messages, toolEvents]);
    const featuredProfile = (manualProfile && profileRoutes[manualProfile]) ? manualProfile : highlightedProf;
    const featuredRoute   = useMemo(() => {
        if (!routes.length) return null;
        if (featuredProfile && profileRoutes[featuredProfile]) return profileRoutes[featuredProfile];
        return routes[routes.length - 1];
    }, [routes, featuredProfile, profileRoutes]);
    // Only compare the featured route against the other routes computed
    // in the same tool call (its own batch) — see extractRouteBatches.
    const featuredSiblings = useMemo(() => {
        if (!featuredRoute) return [];
        const batch = routeBatches.find(b => b.includes(featuredRoute));
        return batch || [featuredRoute];
    }, [routeBatches, featuredRoute]);
    const locations = useMemo(() => extractLocations(messages), [messages]);

    useEffect(() => {
        let cancelled = false;
        getAIStatus()
            .then(data  => { if (!cancelled) setStatus(data); })
            .catch(err  => { if (!cancelled) setStatus({ mcp: 'unavailable', tool_count: 0, tools: [], error: err.message }); });
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        const el = scrollRef.current;
        if (el && typeof el.scrollTo === 'function') {
            el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
        }
    }, [messages, toolEvents]);

    const handleEvent = event => {
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
                    const realIndex  = next.length - 1 - index;
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
    };

    const submit = async prompt => {
        const message = (prompt || input).trim();
        if (!message || loading) return;
        setInput('');
        setLoading(true);
        setManualProfile(null);
        setMessages(prev => [...prev, { role: 'user', content: message }]);
        abortRef.current?.abort();
        abortRef.current = new AbortController();
        try {
            await streamAIChat({ message, sessionId, signal: abortRef.current.signal, onEvent: handleEvent });
        } catch (err) {
            setLoading(false);
            setMessages(prev => [...prev, { role: 'assistant', content: err.message }]);
        }
    };

    return (
        <div className="ai-demo-shell">
            <header className="ai-demo-header">
                <button className="ai-back-btn" onClick={onBack} aria-label="Back to SafeMAPS">
                    Back
                </button>
                <div className="ai-demo-brand">
                    <span className="ai-demo-wordmark">SafeMAPS</span>
                    <span className="ai-demo-tagline">AI route planning</span>
                </div>
            </header>

            <main className="ai-demo-main">
                {/* ── Conversation ── */}
                <section className="ai-chat-panel">
                    <div className="ai-panel-label">Plan a journey</div>

                    <div className="ai-chat-scroll" ref={scrollRef}>
                        {!messages.length && (
                            <div className="ai-empty-state">
                                <p className="ai-empty-lede">
                                    Describe where you want to go in natural language.
                                </p>
                                <div className="ai-suggested">
                                    {PROMPTS.map(prompt => (
                                        <button key={prompt} onClick={() => submit(prompt)}>
                                            “{prompt}”
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        {messages.map((message, index) => (
                            <div key={index} className={`ai-message ${message.role}`}>
                                <span className="ai-message-role">{message.role === 'user' ? 'You' : 'SafeMAPS'}</span>
                                <p>{message.content}</p>
                            </div>
                        ))}

                        {loading && <p className="ai-progress">Understanding request…</p>}

                        {featuredRoute && (
                            <RouteAnalysis route={featuredRoute} allRoutes={featuredSiblings} locations={locations} />
                        )}

                        {toolEvents.length > 0 && (
                            <details className="ai-analysis-details">
                                <summary>Analysis details</summary>
                                <div className="ai-analysis-list">
                                    {toolEvents.map(event => (
                                        <ToolCallCard key={event.id} event={event} />
                                    ))}
                                </div>
                            </details>
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
                    {status.mcp === 'unavailable' && (
                        <p className="ai-status-line">SafeMAPS AI is temporarily unavailable.</p>
                    )}
                </section>

                {/* ── Map ── */}
                <section className="ai-map-panel">
                    <div className="ai-profile-strip">
                        {PROFILES.map(p => {
                            const routeForProfile = profileRoutes[p.id];
                            const isFeatured = featuredProfile === p.id && !!routeForProfile;
                            return (
                                <button
                                    key={p.id}
                                    type="button"
                                    className={`ai-profile-item ${routeForProfile ? 'has-data' : ''} ${isFeatured ? 'featured' : ''}`}
                                    style={{ '--prof-color': p.color }}
                                    onClick={() => routeForProfile && setManualProfile(p.id)}
                                    disabled={!routeForProfile}
                                >
                                    <span className="ai-profile-item-label">{p.label}</span>
                                    <span className="ai-profile-item-value">
                                        {routeForProfile ? `${Math.round(routeForProfile.travel_time_minutes)} min` : '—'}
                                    </span>
                                </button>
                            );
                        })}
                    </div>

                    <AIMap routes={routes} />
                </section>
            </main>
        </div>
    );
}
