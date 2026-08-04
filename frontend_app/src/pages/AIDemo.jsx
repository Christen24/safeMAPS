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

function extractRoutes(toolEvents) {
    const routes = [];
    for (const event of toolEvents) {
        const result = event.result;
        if (!result || event.status !== 'completed') continue;
        if (result.geometry) routes.push(result);
        if (Array.isArray(result.routes)) routes.push(...result.routes.filter(route => route.geometry));
    }
    return routes;
}

export default function AIDemo({ onBack }) {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [toolEvents, setToolEvents] = useState([]);
    const [status, setStatus] = useState({ mcp: 'connecting', tool_count: 0, tools: [] });
    const [sessionId, setSessionId] = useState(() => crypto.randomUUID());
    const [loading, setLoading] = useState(false);
    const abortRef = useRef(null);
    const routes = useMemo(() => extractRoutes(toolEvents), [toolEvents]);

    useEffect(() => {
        let cancelled = false;
        getAIStatus()
            .then(data => { if (!cancelled) setStatus(data); })
            .catch(error => {
                if (!cancelled) setStatus({ mcp: 'unavailable', tool_count: 0, tools: [], error: error.message });
            });
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
                const next = [...prev];
                const index = [...next].reverse().findIndex(item =>
                    item.tool === event.tool && item.status === 'running'
                );
                if (index >= 0) {
                    const realIndex = next.length - 1 - index;
                    next[realIndex] = {
                        ...next[realIndex],
                        status: event.result?.error ? 'failed' : 'completed',
                        duration_ms: event.duration_ms,
                        result: event.result,
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
            setMessages(prev => prev.map(item => ({ ...item, streaming: false })));
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
            await streamAIChat({
                message,
                sessionId,
                signal: abortRef.current.signal,
                onEvent: handleEvent,
            });
        } catch (error) {
            setLoading(false);
            setMessages(prev => [...prev, { role: 'assistant', content: error.message }]);
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
                    <form className="ai-input-bar" onSubmit={event => { event.preventDefault(); submit(); }}>
                        <input
                            value={input}
                            onChange={event => setInput(event.target.value)}
                            placeholder="Ask SafeMAPS..."
                            maxLength={1200}
                        />
                        <button type="submit" disabled={loading || !input.trim()}>
                            Send
                        </button>
                    </form>
                </section>

                <section className="ai-map-panel">
                    <div className="ai-map-toolbar">
                        <div>
                            <span>Route Visualization</span>
                            <b>{routes.length ? `${routes.length} route${routes.length === 1 ? '' : 's'}` : 'Waiting for MCP route result'}</b>
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
