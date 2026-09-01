import { useState } from 'react';

export default function ToolCallCard({ event }) {
    const [open, setOpen] = useState(false);
    const isDone = event.status === 'completed';
    const isFailed = event.status === 'failed';

    return (
        <div className={`ai-tool-card ${isDone ? 'completed' : ''} ${isFailed ? 'failed' : ''}`}>
            <button className="ai-tool-head" onClick={() => setOpen(!open)}>
                <span className="ai-tool-state">{isDone ? 'Done' : isFailed ? 'Failed' : 'Running'}</span>
                <span className="ai-tool-name">{event.tool}</span>
                {event.duration_ms != null && (
                    <span className="ai-tool-time">{event.duration_ms} ms</span>
                )}
            </button>

            {open && (
                <pre className="ai-tool-result">
                    {JSON.stringify(event.result || event.arguments || {}, null, 2)}
                </pre>
            )}
        </div>
    );
}
