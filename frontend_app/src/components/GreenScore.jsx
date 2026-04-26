import { useState, useEffect, useCallback } from 'react';

const API_BASE = '/api';

// ── Session identity ──────────────────────────────────────────────────
// Generate a UUID on first load, persist in localStorage.
// Sent as X-Session-ID on every request to identify the user
// without requiring login.
function getOrCreateSessionId() {
    const key = 'safemaps_session_id';
    let id = localStorage.getItem(key);
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(key, id);
    }
    return id;
}

const SESSION_ID = getOrCreateSessionId();

// ── API helpers ───────────────────────────────────────────────────────
async function fetchGreenScore() {
    const resp = await fetch(`${API_BASE}/user/green-score`, {
        headers: { 'X-Session-ID': SESSION_ID },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
}

async function fetchTripHistory() {
    const resp = await fetch(`${API_BASE}/user/trips?limit=30`, {
        headers: { 'X-Session-ID': SESSION_ID },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
}

// ── Score gauge ───────────────────────────────────────────────────────
function ScoreGauge({ score }) {
    const radius   = 80;
    const stroke   = 12;
    const cx       = 100;
    const cy       = 100;
    const normalised = Math.min(100, Math.max(0, score));
    const circumference = Math.PI * radius;     // half-circle arc length
    const offset = circumference * (1 - normalised / 100);

    const color = score >= 80 ? '#69f6b8'
                : score >= 60 ? '#f59e0b'
                : score >= 40 ? '#f97316'
                : '#ff716c';

    return (
        <div className="gs-gauge-wrap">
            <svg viewBox="0 0 200 120" className="gs-gauge-svg">
                {/* Track */}
                <path
                    d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
                    fill="none"
                    stroke="rgba(255,255,255,0.08)"
                    strokeWidth={stroke}
                    strokeLinecap="round"
                />
                {/* Fill */}
                <path
                    d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
                    fill="none"
                    stroke={color}
                    strokeWidth={stroke}
                    strokeLinecap="round"
                    strokeDasharray={circumference}
                    strokeDashoffset={offset}
                    style={{ transition: 'stroke-dashoffset 1s ease, stroke 0.5s ease' }}
                />
                {/* Score text */}
                <text x={cx} y={cy - 4} textAnchor="middle"
                    fill={color} fontSize="36" fontWeight="700" fontFamily="inherit">
                    {Math.round(normalised)}
                </text>
                <text x={cx} y={cy + 18} textAnchor="middle"
                    fill="rgba(255,255,255,0.5)" fontSize="11" fontFamily="inherit">
                    / 100
                </text>
            </svg>
        </div>
    );
}

// ── Stat card ─────────────────────────────────────────────────────────
function StatCard({ icon, label, value, unit, color }) {
    return (
        <div className="gs-stat-card">
            <span className="gs-stat-icon">{icon}</span>
            <div className="gs-stat-body">
                <div className="gs-stat-value" style={{ color: color || 'var(--primary)' }}>
                    {value}
                    {unit && <span className="gs-stat-unit"> {unit}</span>}
                </div>
                <div className="gs-stat-label">{label}</div>
            </div>
        </div>
    );
}

// ── AQI colour helper ─────────────────────────────────────────────────
function aqiColor(aqi) {
    if (aqi <= 50)  return '#69f6b8';
    if (aqi <= 100) return '#f59e0b';
    if (aqi <= 150) return '#f97316';
    return '#ff716c';
}

// ── Trip row ──────────────────────────────────────────────────────────
function TripRow({ trip }) {
    const profileEmoji = {
        balanced:   '⚖️',
        fastest:    '⚡',
        safest:     '🛡️',
        healthiest: '🌿',
    }[trip.profile] || '📍';

    const date = new Date(trip.created_at);
    const dateStr = date.toLocaleDateString('en-IN', {
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    });

    return (
        <div className="gs-trip-row">
            <div className="gs-trip-left">
                <span className="gs-trip-emoji">{profileEmoji}</span>
                <div className="gs-trip-meta">
                    <div className="gs-trip-profile">{trip.profile}</div>
                    <div className="gs-trip-date">{dateStr}</div>
                </div>
            </div>
            <div className="gs-trip-right">
                <div className="gs-trip-stat">
                    <span style={{ color: aqiColor(trip.avg_aqi) }}>
                        AQI {trip.avg_aqi}
                    </span>
                </div>
                <div className="gs-trip-stat">
                    {trip.distance_km.toFixed(1)} km
                </div>
                <div className="gs-trip-score">
                    +{trip.green_score_delta}
                </div>
            </div>
        </div>
    );
}

// ── Mini bar chart (daily AQI exposure) ───────────────────────────────
function MiniBar({ value, max, color }) {
    const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
    return (
        <div className="gs-bar-wrap">
            <div className="gs-bar-fill" style={{ height: `${pct}%`, background: color }} />
        </div>
    );
}

function ExposureChart({ trips }) {
    if (!trips || trips.length === 0) return null;

    // Group by day, average AQI per day
    const byDay = {};
    trips.forEach(t => {
        const day = t.created_at.slice(0, 10);
        if (!byDay[day]) byDay[day] = { total: 0, count: 0 };
        byDay[day].total += t.avg_aqi;
        byDay[day].count += 1;
    });

    const days = Object.keys(byDay).sort().slice(-14);   // last 14 days
    const values = days.map(d => byDay[d].total / byDay[d].count);
    const maxVal = Math.max(...values, 50);

    return (
        <div className="gs-chart-section">
            <div className="gs-section-title">14-day AQI exposure</div>
            <div className="gs-chart-bars">
                {days.map((d, i) => (
                    <div key={d} className="gs-chart-col">
                        <MiniBar
                            value={values[i]}
                            max={maxVal}
                            color={aqiColor(values[i])}
                        />
                        <div className="gs-chart-label">
                            {new Date(d).toLocaleDateString('en-IN', { day: 'numeric' })}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ── Main component ────────────────────────────────────────────────────
export default function GreenScore() {
    const [scoreData, setScoreData]   = useState(null);
    const [tripData, setTripData]     = useState(null);
    const [loading, setLoading]       = useState(true);
    const [error, setError]           = useState(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [score, trips] = await Promise.all([
                fetchGreenScore(),
                fetchTripHistory(),
            ]);
            setScoreData(score);
            setTripData(trips);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    if (loading) {
        return (
            <div className="gs-loading">
                <div className="loading-ring" />
                <p>Loading your health report...</p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="gs-error">
                <p>⚠️ Could not load Green Score: {error}</p>
                <button className="gs-retry-btn" onClick={load}>Retry</button>
            </div>
        );
    }

    const s = scoreData;
    const monthLabel = s?.month
        ? new Date(s.month + '-01').toLocaleDateString('en-IN', { month: 'long', year: 'numeric' })
        : '';

    return (
        <div className="gs-container">

            {/* Header */}
            <div className="gs-header">
                <div>
                    <h2 className="gs-title">🌿 My Health Report</h2>
                    <p className="gs-month">{monthLabel}</p>
                </div>
                <button className="gs-refresh-btn" onClick={load}>↻ Refresh</button>
            </div>

            {/* Score gauge + grade */}
            <div className="gs-score-section">
                <ScoreGauge score={s?.green_score ?? 0} />
                <div className="gs-grade-wrap">
                    <div className="gs-grade">{s?.grade}</div>
                    <div className="gs-tip">{s?.tip}</div>
                </div>
            </div>

            {/* Stat cards */}
            <div className="gs-stats-grid">
                <StatCard
                    icon="🛣️" label="Total distance"
                    value={(s?.total_km ?? 0).toFixed(1)} unit="km"
                    color="#699cff"
                />
                <StatCard
                    icon="🌫️" label="AQI exposure saved"
                    value={(s?.aqi_saved_total ?? 0).toFixed(0)} unit="AQI·min"
                    color="#69f6b8"
                />
                <StatCard
                    icon="🫁" label="PM2.5 avoided"
                    value={(s?.pm25_ug_saved ?? 0).toFixed(0)} unit="µg"
                    color="#f59e0b"
                />
                <StatCard
                    icon="⚠️" label="Hotspots avoided"
                    value={s?.hotspots_avoided ?? 0}
                    color="#ff716c"
                />
                <StatCard
                    icon="📍" label="Trips this month"
                    value={s?.total_trips ?? 0}
                    color="#c180ff"
                />
                <StatCard
                    icon="⏱️" label="Time vs fastest"
                    value={(s?.time_delta_min ?? 0) > 0
                        ? `+${(s.time_delta_min).toFixed(0)}`
                        : (s?.time_delta_min ?? 0).toFixed(0)}
                    unit="min"
                    color="var(--on-surface-variant)"
                />
            </div>

            {/* 14-day AQI exposure chart */}
            <ExposureChart trips={tripData?.trips ?? []} />

            {/* Trip history */}
            <div className="gs-trips-section">
                <div className="gs-section-title">Recent trips</div>
                {(tripData?.trips ?? []).length === 0 ? (
                    <div className="gs-no-trips">
                        No trips recorded yet. Compute a route on the Dashboard to start tracking!
                    </div>
                ) : (
                    <div className="gs-trips-list">
                        {tripData.trips.map(t => <TripRow key={t.id} trip={t} />)}
                    </div>
                )}
            </div>

        </div>
    );
}

// ── Export session ID so App.jsx can pass it to trip recording ────────
export { SESSION_ID };
