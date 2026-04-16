/**
 * Landing Page — inspired by Stitch "Obsidian Emerald" design system.
 * Premium onboarding experience with hero + feature cards.
 */

export default function LandingPage({ onStart }) {
    return (
        <div className="app landing-mode">
            {/* Hero Section */}
            <div className="landing-hero">
                <h1 className="fade-in-up">
                    Breathe Easy.<br />Travel <span>Safe</span>.
                </h1>
                <p className="subtitle fade-in-up" style={{ animationDelay: '0.1s' }}>
                    AI-powered routing that minimizes your exposure to air pollution
                    and accident-prone roads across Bengaluru.
                </p>
                <div className="landing-ctas fade-in-up" style={{ animationDelay: '0.2s' }}>
                    <button className="landing-cta-primary" onClick={onStart}>
                        🗺️ Plan Your Route
                    </button>
                    <button className="landing-cta-secondary" onClick={() => {
                        document.querySelector('.features-section')?.scrollIntoView({ behavior: 'smooth' });
                    }}>
                        Learn How It Works
                    </button>
                </div>
            </div>

            {/* Features Section */}
            <div className="features-section">
                <div className="features-title">How SafeMAPS Protects You</div>
                <div className="features-grid">
                    <div className="feature-card fade-in-up" style={{ animationDelay: '0.3s' }}>
                        <div className="feature-icon">🌫️</div>
                        <div className="feature-title">Clean Air Routes</div>
                        <p className="feature-desc">
                            Routes that avoid high-pollution zones, using real-time AQI data
                            from 10+ monitoring stations across Bengaluru.
                        </p>
                    </div>

                    <div className="feature-card fade-in-up" style={{ animationDelay: '0.4s' }}>
                        <div className="feature-icon">🛡️</div>
                        <div className="feature-title">Accident Avoidance</div>
                        <p className="feature-desc">
                            Smart routing around 20+ identified blackspots using
                            Bangalore Traffic Police data and historical analysis.
                        </p>
                    </div>

                    <div className="feature-card fade-in-up" style={{ animationDelay: '0.5s' }}>
                        <div className="feature-icon">⚖️</div>
                        <div className="feature-title">Your Priorities, Your Route</div>
                        <p className="feature-desc">
                            Customize weights for speed, air quality, and safety to get the
                            perfect route tailored for you.
                        </p>
                    </div>
                </div>
            </div>

            {/* Footer */}
            <div className="landing-footer">
                Built for Bengaluru · Powered by OpenStreetMap · v0.1.0
            </div>
        </div>
    );
}
