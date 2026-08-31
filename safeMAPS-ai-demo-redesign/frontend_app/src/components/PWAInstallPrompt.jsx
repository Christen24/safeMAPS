/**
 * SafeMAPS — PWA Install Prompt Component
 *
 * Shows a "Install App" bottom sheet on mobile when the browser fires
 * the `beforeinstallprompt` event. Dismisses silently if the user says no.
 * Never shown on desktop (uses window.matchMedia check).
 *
 * Usage: mount once in App.jsx top-level.
 */

import { useState, useEffect } from 'react';

export default function PWAInstallPrompt() {
    const [deferredPrompt, setDeferredPrompt] = useState(null);
    const [visible,         setVisible]        = useState(false);
    const [dismissed,       setDismissed]      = useState(false);

    useEffect(() => {
        // Check if already dismissed this session
        if (sessionStorage.getItem('pwa_prompt_dismissed')) return;

        const handler = (e) => {
            e.preventDefault();
            setDeferredPrompt(e);
            // Only show on mobile-ish screens
            if (window.innerWidth < 900) {
                setTimeout(() => setVisible(true), 3000); // 3s delay
            }
        };

        window.addEventListener('beforeinstallprompt', handler);
        return () => window.removeEventListener('beforeinstallprompt', handler);
    }, []);

    const handleInstall = async () => {
        if (!deferredPrompt) return;
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        if (outcome === 'accepted') {
            setVisible(false);
            setDeferredPrompt(null);
        }
    };

    const handleDismiss = () => {
        setVisible(false);
        setDismissed(true);
        sessionStorage.setItem('pwa_prompt_dismissed', '1');
    };

    if (!visible || dismissed) return null;

    return (
        <div className="pwa-prompt" role="dialog" aria-label="Install SafeMAPS">
            <div className="pwa-prompt-icon">
                <img src="/icons/icon-96.png" alt="SafeMAPS" width={40} height={40} />
            </div>
            <div className="pwa-prompt-text">
                <span className="pwa-prompt-title">Install SafeMAPS</span>
                <span className="pwa-prompt-sub">Add to home screen for offline routing</span>
            </div>
            <button className="pwa-install-btn" onClick={handleInstall}>
                Install
            </button>
            <button className="pwa-dismiss-btn" onClick={handleDismiss} aria-label="Dismiss">
                ✕
            </button>
        </div>
    );
}
