import { useState, useEffect, useCallback } from 'react';

const API_BASE = '/api';

function getOrCreateSessionId() {
    const key = 'safemaps_session_id';
    let id = localStorage.getItem(key);
    if (!id) { id = crypto.randomUUID(); localStorage.setItem(key, id); }
    return id;
}

export const SESSION_ID = getOrCreateSessionId();

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

// ── Score gauge ───────────────────────────────────────────────
function ScoreGauge({ score }) {
    const radius      = 80;
    const stroke      = 10;
    const cx = 100, cy = 100;
    const norm        = Math.min(100, Math.max(0, score));
    const circumference = Math.PI * radius;
    const offset      = circumference * (1 - norm / 100);

    const color = score >= 80 ? '#00ff88'
                : score >= 60 ? '#ffb830'
                : score >= 40 ? '#ff8c00'
                : '#ff4560';

    return (
        <div className="gs-gauge-wrap">
            <svg viewBox="0 0 200 120" className="gs-gauge-svg">
                {/* Track */}
                <path
                    d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
                    fill="none" stroke="rgba(93,184,255,0.06)"
                    strokeWidth={stroke} strokeLinecap="round"
                />
                {/* Fill */}
                <path
                    d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
                    fill="none" stroke={color}
                    strokeWidth={stroke} strokeLinecap="round"
                    strokeDasharray={circumference}
                    strokeDashoffset={offset}
                    style={{ transition: 'stroke-dashoffset 1.2s cubic-bezier(0.4,0,0.2,1), stroke 0.5s ease' }}
                    filter={`drop-shadow(0 0 6px ${color}60)`}
                />
                {/* Score */}
                <text x={cx} y={cy - 6} textAnchor="middle"
                    fill={color} fontSize="38" fontWeight="700"
                    fontFamily="JetBrains Mono, monospace">
                    {Math.round(norm)}
                </text>
                <text x={cx} y={cy + 16} textAnchor="middle"
                    fill="rgba(107,122,153,0.8)" fontSize="10"
                    fontFamily="JetBrains Mono, monospace" letterSpacing="2">
                    / 100
                </text>
            </svg>
        </div>
    );
}

// ── Stat card ─────────────────────────────────────────────────
function StatCard({ icon, label, value, unit, color }) {
    return (
        <div className="gs-stat-card">
            <span className="gs-stat-icon">{icon}</span>
            <div className="gs-stat-body">
                <div className="gs-stat-value" style={{ color: color || 'var(--acid)' }}>
                    {value}
                    {unit && <span className="gs-stat-unit"> {unit}</span>}
                </div>
                <div className="gs-stat-label">{label}</div>
            </div>
        </div>
    );
}

function aqiColor(aqi) {
    if (aqi <= 50)  return '#00ff88';
    if (aqi <= 100) return '#ffb830';
    if (aqi <= 150) return '#ff8c00';
    return '#ff4560';
}

// ── Trip row ──────────────────────────────────────────────────
function TripRow({ trip }) {
    const emoji = { balanced:'⚖️', fastest:'⚡', safest:'🛡️', healthiest:'🫁' }[trip.profile] || '◎';
    const date  = new Date(trip.created_at);
    const str   = date.toLocaleDateString('en-IN', {
        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    });

    return (
        <div className="gs-trip-row">
            <div className="gs-trip-left">
                <span className="gs-trip-emoji">{emoji}</span>
                <div>
                    <div className="gs-trip-profile">{trip.profile.toUpperCase()}</div>
                    <div className="gs-trip-date">{str}</div>
                </div>
            </div>
            <div className="gs-trip-right">
                <span className="gs-trip-stat" style={{ color: aqiColor(trip.avg_aqi) }}>
                    AQI {trip.avg_aqi}
                </span>
                <span className="gs-trip-stat">{trip.distance_km.toFixed(1)} km</span>
                <span className="gs-trip-score">+{trip.green_score_delta}</span>
            </div>
        </div>
    );
}

// ── Bar chart ─────────────────────────────────────────────────
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
    const byDay = {};
    trips.forEach(t => {
        const day = t.created_at.slice(0, 10);
        if (!byDay[day]) byDay[day] = { total: 0, count: 0 };
        byDay[day].total += t.avg_aqi;
        byDay[day].count += 1;
    });
    const days   = Object.keys(byDay).sort().slice(-14);
    const values = days.map(d => byDay[d].total / byDay[d].count);
    const maxVal = Math.max(...values, 50);

    return (
        <div className="gs-chart-section">
            <div className="gs-section-title">14-Day AQI Exposure</div>
            <div className="gs-chart-bars">
                {days.map((d, i) => (
                    <div key={d} className="gs-chart-col">
                        <MiniBar value={values[i]} max={maxVal} color={aqiColor(values[i])} />
                        <div className="gs-chart-label">
                            {new Date(d).toLocaleDateString('en-IN', { day: 'numeric' })}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ── Main ──────────────────────────────────────────────────────
export default function GreenScore() {
    const [scoreData, setScoreData] = useState(null);
    const [tripData,  setTripData]  = useState(null);
    const [loading,   setLoading]   = useState(true);
    const [error,     setError]     = useState(null);

    const load = useCallback(async () => {
        setLoading(true); setError(null);
        try {
            const [score, trips] = await Promise.all([fetchGreenScore(), fetchTripHistory()]);
            setScoreData(score);
            setTripData(trips);
        } catch (err) { setError(err.message); }
        finally { setLoading(false); }
    }, []);

    useEffect(() => { load(); }, [load]);

    if (loading) return (
        <div className="gs-loading">
            <div className="loading-ring" />
            <p>Loading health report…</p>
        </div>
    );

    if (error) return (
        <div className="gs-error">
            <p>⚠ {error}</p>
            <button className="gs-retry-btn" onClick={load}>Retry</button>
        </div>
    );

    const s = scoreData;
    const monthLabel = s?.month
        ? new Date(s.month + '-01').toLocaleDateString('en-IN', { month: 'long', year: 'numeric' })
        : '';

    return (
        <div className="gs-container">

            {/* Header */}
            <div className="gs-header">
                <div>
                    <h2 className="gs-title">Health Intelligence</h2>
                    <p className="gs-month">{monthLabel?.toUpperCase()}</p>
                </div>
                <button className="gs-refresh-btn" onClick={load}>↻ Refresh</button>
            </div>

            {/* Score gauge */}
            <div className="gs-score-section">
                <ScoreGauge score={s?.green_score ?? 0} />
                <div className="gs-grade-wrap">
                    <div className="gs-grade">{s?.grade}</div>
                    <div className="gs-tip">{s?.tip}</div>
                </div>
            </div>

            {/* Stat cards */}
            <div className="gs-stats-grid">
                <StatCard icon="🛣️" label="Total Dist."     value={(s?.total_km ?? 0).toFixed(1)}         unit="km"      color="var(--ice)"    />
                <StatCard icon="🌫️" label="AQI Saved"       value={(s?.aqi_saved_total ?? 0).toFixed(0)}   unit="AQI·min" color="var(--acid)"   />
                <StatCard icon="🫁" label="PM2.5 Avoided"   value={(s?.pm25_ug_saved ?? 0).toFixed(0)}     unit="µg"      color="var(--amber)"  />
                <StatCard icon="⚠️" label="Spots Avoided"   value={s?.hotspots_avoided ?? 0}                              color="var(--infra)"  />
                <StatCard icon="◎"  label="Trips / Month"   value={s?.total_trips ?? 0}                                    color="var(--violet)" />
                <StatCard icon="⏱️" label="Time vs Fastest" value={(s?.time_delta_min ?? 0) > 0 ? `+${(s.time_delta_min).toFixed(0)}` : (s?.time_delta_min ?? 0).toFixed(0)} unit="min" color="var(--text-secondary)" />
            </div>

            {/* Chart */}
            <ExposureChart trips={tripData?.trips ?? []} />

            {/* Trip history */}
            <div className="gs-trips-section">
                <div className="gs-section-title">Recent Trips</div>
                {(tripData?.trips ?? []).length === 0 ? (
                    <div className="gs-no-trips">
                        No trips recorded yet.<br />
                        Compute a route on the Dashboard to begin tracking.
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
