import { useEffect, useMemo, useRef, useState } from 'react';
import AIMap from '../components/ai/AIMap';
import ToolCallCard from '../components/ai/ToolCallCard';
import { getAIStatus, streamAIChat } from '../services/chatApi';

const PROMPTS = [
    'Safest route from Koramangala to Whitefield',
    'Compare safest vs fastest route from Indiranagar to Electronic City',
    "What's the AQI around MG Road?",
    'Show accident risk near Hebbal',
];

// ── Routing profiles — mirrors primary page ───────────────────
const PROFILES = [
    { id: 'fastest',    icon: '⚡', label: 'Fastest',    sub: 'Min. Time',     color: '#4fc3e0' },
    { id: 'safest',     icon: '🛡️', label: 'Safest',     sub: 'Max. Security', color: '#4ecb8d' },
    { id: 'healthiest', icon: '🫁', label: 'Healthiest', sub: 'Low AQI',       color: '#f0a93e' },
    { id: 'balanced',   icon: '⚖️', label: 'Balanced',   sub: 'Weighted',      color: '#9b87e8' },
];

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

// Detect which profiles are present in the returned routes
function getActiveProfiles(routes) {
    const ids = new Set(routes.map(r => r.profile || r.route_id).filter(Boolean));
    return ids;
}

// Find the most recently mentioned profile from AI messages/tool calls
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

export default function AIDemo({ onBack }) {
    const [messages, setMessages]     = useState([]);
    const [input, setInput]           = useState('');
    const [toolEvents, setToolEvents] = useState([]);
    const [status, setStatus]         = useState({ mcp: 'connecting', tool_count: 0, tools: [] });
    const [sessionId, setSessionId]   = useState(() => crypto.randomUUID());
    const [loading, setLoading]       = useState(false);
    const abortRef                    = useRef(null);

    const routes          = useMemo(() => extractRoutes(toolEvents), [toolEvents]);
    const activeProfiles  = useMemo(() => getActiveProfiles(routes), [routes]);
    const highlightedProf = useMemo(() => inferHighlightedProfile(messages, toolEvents), [messages, toolEvents]);

    useEffect(() => {
        let cancelled = false;
        getAIStatus()
            .then(data  => { if (!cancelled) setStatus(data); })
            .catch(err  => { if (!cancelled) setStatus({ mcp: 'unavailable', tool_count: 0, tools: [], error: err.message }); });
        return () => { cancelled = true; };
    }, []);

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
                <div className="ai-demo-brand">
                    <button className="ai-back-btn" onClick={onBack} aria-label="Back to SafeMAPS">
                        Back
                    </button>
                    <div>
                        <h1>SafeMAPS AI</h1>
                        <p>AI-powered safer routing using Model Context Protocol</p>
                    </div>
                </div>
                <div className={`ai-mcp-status ${status.mcp}`}>
                    <span />
                    MCP {status.mcp === 'connected' ? 'Connected' : status.mcp === 'unavailable' ? 'Unavailable' : 'Connecting'}
                    {status.tool_count ? <b>{status.tool_count} tools</b> : null}
                </div>
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

                        {messages.map((message, index) => (
                            <div key={index} className={`ai-message ${message.role}`}>
                                <span>{message.role === 'user' ? 'You' : 'SafeMAPS AI'}</span>
                                <p>{message.content}</p>
                            </div>
                        ))}

                        {toolEvents.map(event => (
                            <ToolCallCard key={event.id} event={event} />
                        ))}

                        {loading && <div className="ai-progress">Understanding request...</div>}
                    </div>

                    <form className="ai-input-bar" onSubmit={e => { e.preventDefault(); submit(); }}>
                        <input
                            value={input}
                            onChange={e => setInput(e.target.value)}
                            placeholder="Ask SafeMAPS..."
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
                            const isActive    = activeProfiles.has(p.id);
                            const isHighlight = highlightedProf === p.id;
                            return (
                                <div
                                    key={p.id}
                                    className={`ai-profile-card ${isActive ? 'active' : ''} ${isHighlight ? 'highlight' : ''}`}
                                    style={{ '--prof-color': p.color }}
                                >
                                    <span className="ai-profile-icon">{p.icon}</span>
                                    <span className="ai-profile-label">{p.label}</span>
                                    <span className="ai-profile-sub">{p.sub}</span>
                                    {isActive && <span className="ai-profile-dot" />}
                                </div>
                            );
                        })}
                    </div>

                    {/* Map toolbar */}
                    <div className="ai-map-toolbar">
                        <div>
                            <span>Route Visualization</span>
                            <b>{routes.length ? `${routes.length} route${routes.length === 1 ? '' : 's'}` : 'Waiting for MCP result'}</b>
                        </div>
                        <details className="ai-tools-menu">
                            <summary>MCP Tools</summary>
                            <ul>
                                {(status.tools || []).map(tool => <li key={tool}>{tool}</li>)}
                            </ul>
                        </details>
                    </div>

                    <AIMap routes={routes} />
                </section>
            </main>
        </div>
    );
}
