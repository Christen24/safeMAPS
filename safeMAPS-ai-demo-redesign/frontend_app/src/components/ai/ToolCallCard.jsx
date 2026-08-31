import { useState } from 'react';

// Human-readable labels for MCP tool names, shown instead of raw
// snake_case identifiers in the "Analysis details" trace.
const TOOL_LABELS = {
    get_safe_route:          'Route analysis',
    compare_route_profiles:  'Route comparison',
    explain_route_cost:      'Route cost breakdown',
    get_aqi_near:            'Air quality lookup',
    get_aqi_heatmap_summary: 'Air quality summary',
    predict_aqi_near:        'Air quality forecast',
    get_accident_risk_near:  'Accident risk lookup',
};

function humanizeTool(name = '') {
    return TOOL_LABELS[name] || name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// Coordinates and other purely technical fields are never shown in the
// primary UI (the implementation still sends/receives them normally —
// only this presentation layer is simplified, per the design brief).
const HIDDEN_KEYS = new Set([
    'geometry', 'coordinates', 'weights_used',
    'lat', 'lon', 'origin_lat', 'origin_lon', 'dest_lat', 'dest_lon',
    'station_lat', 'station_lon', 'center_lat', 'center_lon',
]);

function summaryRows(result) {
    if (!result || typeof result !== 'object') return [];
    return Object.entries(result)
        .filter(([key, value]) =>
            !HIDDEN_KEYS.has(key) &&
            value !== null && value !== undefined &&
            typeof value !== 'object'
        )
        .map(([key, value]) => ({ key: key.replace(/_/g, ' '), value: String(value) }));
}

export default function ToolCallCard({ event }) {
    const [open, setOpen] = useState(false);
    const isDone   = event.status === 'completed';
    const isFailed = event.status === 'failed';
    const rows     = isDone ? summaryRows(event.result) : [];

    return (
        <div className="ai-tool-row">
            <button
                type="button"
                className="ai-tool-row-head"
                onClick={() => setOpen(o => !o)}
                disabled={!rows.length}
            >
                <span className={`ai-tool-mark ${isFailed ? 'failed' : isDone ? 'done' : 'pending'}`}>
                    {isFailed ? '✕' : isDone ? '✓' : '···'}
                </span>
                <span className="ai-tool-row-name">{humanizeTool(event.tool)}</span>
                {event.duration_ms != null && (
                    <span className="ai-tool-row-time">{event.duration_ms} ms</span>
                )}
            </button>

            {open && rows.length > 0 && (
                <div className="ai-tool-row-detail">
                    {rows.map(row => (
                        <div key={row.key} className="ai-tool-row-detail-item">
                            <span className="ai-tool-row-detail-key">{row.key}</span>
                            <span className="ai-tool-row-detail-value">{row.value}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
