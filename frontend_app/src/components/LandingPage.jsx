import { memo } from 'react';

const FEATURES = [
    {
        n: '01',
        icon: '🌫️',
        title: 'Live Air Quality',
        desc: 'Real-time PM2.5 and AQI from 10+ Bangalore CPCB stations, interpolated across a 100m grid. Routes avoid prolonged exposure zones.',
    },
    {
        n: '02',
        icon: '🛡️',
        title: 'Accident Avoidance',
        desc: 'Historical blackspot data from Bangalore Traffic Police mapped to road segments. Risk doubles near school zones at drop-off hours.',
    },
    {
        n: '03',
        icon: '⚡',
        title: 'Multi-Profile A*',
        desc: 'Four concurrent route profiles — Fastest, Safest, Healthiest, Balanced — computed in parallel with custom α β γ blending.',
    },
    {
        n: '04',
        icon: '🧠',
        title: 'AQI Forecasting',
        desc: 'LSTM model trained on historical readings predicts air quality 30 minutes ahead — future departures route with predicted conditions.',
    },
    {
        n: '05',
        icon: '🌿',
        title: 'Green Score',
        desc: 'Monthly health report tracking pollutants avoided, hotspots skirted, and PM2.5 saved vs always taking the fastest route.',
    },
    {
        n: '06',
        icon: '⏱️',
        title: 'Departure Time',
        desc: 'Set a future departure and the router applies time-of-day risk multipliers — peak corridors, overnight truck lanes, school zones.',
    },
    {
        n: '07',
        icon: '🔀',
        title: 'Bidirectional A*',
        desc: 'Phase 11 engine searches forward from origin and backward from destination simultaneously — halves explored nodes on routes >5km.',
    },
    {
        n: '08',
        icon: '📡',
        title: 'Live Incidents',
        desc: 'Tri-source incident scraper — OSM Overpass, Waze CCP, and BTP Twitter — fuses and deduplicates events within 100m, expires after 2h.',
    },
];

const STATS = [
    { value: '10+',   label: 'CPCB Stations' },
    { value: '30',    label: 'Blackspots' },
    { value: '4',     label: 'Route Profiles' },
    { value: '15min', label: 'AQI Refresh' },
    { value: 'BiDir', label: 'A* Engine' },
    { value: 'PWA',   label: 'Offline Ready' },
];

const LandingPage = memo(function LandingPage({ onStart }) {
    return (
        <div className="app landing-mode">
            {/* Hero */}
            <div className="landing-hero">
                <div className="hero-system-tag fade-in-up">
                    SAFEMAPS · BENGALURU HEALTH ROUTING SYSTEM · v0.5
                </div>

                <h1 className="fade-in-up" style={{ animationDelay: '0.08s' }}>
                    Route by<br /><span>Health</span>, not just Speed.
                </h1>

                <p className="landing-subtitle fade-in-up" style={{ animationDelay: '0.16s' }}>
                    AI-powered navigation that factors in live air quality, accident
                    blackspots, real-time incidents, and bidirectional A* routing to find
                    the route that's genuinely better for your body.
                </p>

                <div className="landing-pills fade-in-up" style={{ animationDelay: '0.20s' }}>
                    <span className="landing-pill">🔀 BiDir A*</span>
                    <span className="landing-pill">📡 Live Incidents</span>
                    <span className="landing-pill">📲 PWA Ready</span>
                    <span className="landing-pill">🔗 Shareable Routes</span>
                </div>

                <div className="landing-ctas fade-in-up" style={{ animationDelay: '0.28s' }}>
                    <button className="landing-cta-primary" onClick={onStart}>
                        <span>▶ Launch Dashboard</span>
                    </button>
                    <button
                        className="landing-cta-secondary"
                        onClick={() =>
                            document.querySelector('.features-section')
                                ?.scrollIntoView({ behavior: 'smooth' })
                        }
                    >
                        How it works ↓
                    </button>
                </div>

                {/* Stats strip */}
                <div className="landing-stats fade-in-up" style={{ animationDelay: '0.36s' }}>
                    {STATS.map(s => (
                        <div className="landing-stat" key={s.label}>
                            <span className="landing-stat-value">{s.value}</span>
                            <span className="landing-stat-label">{s.label}</span>
                        </div>
                    ))}
                </div>
            </div>

            {/* Features */}
            <div className="features-section">
                <div className="features-header">
                    <div className="features-title">System Capabilities</div>
                    <div className="features-subtitle">
                        Every module runs independently and degrades gracefully when data sources are unavailable.
                    </div>
                </div>
                <div className="features-grid">
                    {FEATURES.map((f) => (
                        <div className="feature-card fade-in-up" key={f.n}>
                            <span className="feature-number">{f.n}</span>
                            <span className="feature-icon">{f.icon}</span>
                            <div className="feature-title">{f.title}</div>
                            <p className="feature-desc">{f.desc}</p>
                        </div>
                    ))}
                </div>
            </div>

            <div className="landing-footer">
                SAFEMAPS v0.5.0 · Bengaluru · OSM · CPCB · WAQI · BiDir A* · MIT License
            </div>
        </div>
    );
});

export default LandingPage;
