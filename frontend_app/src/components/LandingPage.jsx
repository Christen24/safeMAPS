export default function LandingPage({ onStart }) {
    return (
        <div className="app landing-mode">
            {/* Hero */}
            <div className="landing-hero">
                <div className="hero-system-tag fade-in-up">
                    SAFEMAPS · BENGALURU HEALTH ROUTING SYSTEM
                </div>

                <h1 className="fade-in-up" style={{ animationDelay: '0.08s' }}>
                    Route by<br /><span>Health</span>, not just Speed.
                </h1>

                <p className="landing-subtitle fade-in-up" style={{ animationDelay: '0.16s' }}>
                    AI-powered navigation that factors in live air quality, accident
                    blackspots, and real-time traffic to find the route that's genuinely
                    better for your body.
                </p>

                <div className="landing-ctas fade-in-up" style={{ animationDelay: '0.24s' }}>
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
                        How it works
                    </button>
                </div>

                {/* Live stats strip */}
                <div className="landing-stats fade-in-up" style={{ animationDelay: '0.32s' }}>
                    <div className="landing-stat">
                        <span className="landing-stat-value">10+</span>
                        <span className="landing-stat-label">AQI Stations</span>
                    </div>
                    <div className="landing-stat">
                        <span className="landing-stat-value">30</span>
                        <span className="landing-stat-label">Blackspots Mapped</span>
                    </div>
                    <div className="landing-stat">
                        <span className="landing-stat-value">4</span>
                        <span className="landing-stat-label">Route Profiles</span>
                    </div>
                    <div className="landing-stat">
                        <span className="landing-stat-value">15m</span>
                        <span className="landing-stat-label">AQI Refresh</span>
                    </div>
                </div>
            </div>

            {/* Features */}
            <div className="features-section">
                <div className="features-title">System Capabilities</div>
                <div className="features-grid">
                    {[
                        {
                            n: '01',
                            icon: '🌫️',
                            title: 'Live Air Quality',
                            desc: 'Real-time PM2.5 and AQI data from 10+ Bangalore stations, interpolated across a 100m grid. Routes avoid prolonged exposure zones.',
                        },
                        {
                            n: '02',
                            icon: '🛡️',
                            title: 'Accident Avoidance',
                            desc: 'Historical blackspot data from Bangalore Traffic Police mapped to road segments. Risk doubles near school zones at drop-off times.',
                        },
                        {
                            n: '03',
                            icon: '⚡',
                            title: 'Multi-Profile A*',
                            desc: 'Four concurrent route profiles computed in parallel — Fastest, Safest, Healthiest, Balanced. Choose or blend with custom α β γ weights.',
                        },
                        {
                            n: '04',
                            icon: '🧠',
                            title: 'AQI Forecasting',
                            desc: 'LSTM model trained on historical readings predicts air quality 30 minutes ahead, so future departures route with predicted conditions.',
                        },
                        {
                            n: '05',
                            icon: '🌿',
                            title: 'Green Score',
                            desc: 'Monthly health report tracking pollutants avoided, hotspots skirted, and PM2.5 saved versus always taking the fastest route.',
                        },
                        {
                            n: '06',
                            icon: '⏱️',
                            title: 'Departure Time',
                            desc: 'Set a future departure and the router applies time-of-day risk multipliers — peak hour corridors, overnight truck lanes, school zones.',
                        },
                    ].map((f) => (
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
                SAFEMAPS v0.4.0 · Built for Bengaluru · OpenStreetMap · WAQI · MIT License
            </div>
        </div>
    );
}
