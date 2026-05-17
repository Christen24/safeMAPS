/**
 * SafeMAPS — Saved Commutes Panel Component
 *
 * Displays up to 5 saved commutes in the sidebar.
 * One click loads the commute and triggers route computation.
 * Shows live AQI trend badge vs 7-day average for each commute origin.
 */

import { useState, useEffect, useCallback } from 'react';
import {
    getSavedCommutes,
    saveCommute,
    deleteCommute,
    commuteExists,
} from '../utils/savedCommutes';
import { useAQITrend } from '../utils/useAQITrend';

const PROFILE_ICONS = {
    fastest:    '⚡',
    safest:     '🛡️',
    healthiest: '🫁',
    balanced:   '⚖️',
};

// ── Individual commute item with AQI trend badge ──────────────────────
function CommuteItem({ c, onLoad, onDelete }) {
    const { trend } = useAQITrend(c.origin?.lat, c.origin?.lon);

    return (
        <div
            className="saved-item"
            onClick={() => onLoad(c)}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && onLoad(c)}
        >
            <div className="saved-item-main">
                <span className="saved-item-icon">
                    {PROFILE_ICONS[c.profile] || '◈'}
                </span>
                <div className="saved-item-text">
                    <span className="saved-item-name">{c.name}</span>
                    <span className="saved-item-meta">
                        {c.profile} · {new Date(c.savedAt).toLocaleDateString('en-IN', {
                            month: 'short', day: 'numeric',
                        })}
                    </span>
                    {trend && (
                        <span className={`saved-aqi-badge ${trend.direction}`}>
                            {trend.direction === 'worse' ? '▲' : trend.direction === 'better' ? '▼' : '▬'}
                            {' '}{trend.label}
                        </span>
                    )}
                </div>
            </div>
            <button
                className="saved-delete-btn"
                onClick={e => onDelete(c.id, e)}
                title="Delete commute"
                aria-label="Delete"
            >
                ✕
            </button>
        </div>
    );
}


// ── Panel ─────────────────────────────────────────────────────────────
export default function SavedCommutesPanel({ origin, destination, profile, onLoad, isActive }) {
    const [commutes,  setCommutes]  = useState([]);
    const [saveName,  setSaveName]  = useState('');
    const [saving,    setSaving]    = useState(false);
    const [justSaved, setJustSaved] = useState(false);

    useEffect(() => {
        setCommutes(getSavedCommutes());
    }, []);

    const alreadySaved = commuteExists(origin, destination);
    const canSave = isActive && origin?.lat && destination?.lat && !alreadySaved;

    const handleSave = useCallback(() => {
        const name = saveName.trim() || `Commute ${commutes.length + 1}`;
        saveCommute(name, origin, destination, profile);
        setSaveName('');
        setSaving(false);
        setJustSaved(true);
        setCommutes(getSavedCommutes());
        setTimeout(() => setJustSaved(false), 1800);
    }, [saveName, origin, destination, profile, commutes.length]);

    const handleDelete = useCallback((id, e) => {
        e.stopPropagation();
        deleteCommute(id);
        setCommutes(getSavedCommutes());
    }, []);

    const handleLoad = useCallback((c) => {
        onLoad(c.origin, c.destination, c.profile);
    }, [onLoad]);

    return (
        <div className="saved-panel">
            <div className="saved-panel-header">
                <span className="saved-panel-title">Saved Commutes</span>
                <span className="saved-panel-count">{commutes.length}/5</span>
            </div>

            {commutes.length === 0 ? (
                <p className="saved-empty">No saved commutes. Compute a route and save it.</p>
            ) : (
                <div className="saved-list">
                    {commutes.map(c => (
                        <CommuteItem
                            key={c.id}
                            c={c}
                            onLoad={handleLoad}
                            onDelete={handleDelete}
                        />
                    ))}
                </div>
            )}

            {canSave && commutes.length < 5 && (
                <div className="saved-save-row">
                    {saving ? (
                        <div className="saved-name-row">
                            <input
                                autoFocus
                                className="saved-name-input"
                                placeholder="Name this commute…"
                                value={saveName}
                                onChange={e => setSaveName(e.target.value)}
                                onKeyDown={e => {
                                    if (e.key === 'Enter') handleSave();
                                    if (e.key === 'Escape') setSaving(false);
                                }}
                                maxLength={30}
                            />
                            <button className="saved-confirm-btn" onClick={handleSave}>✓</button>
                            <button className="saved-cancel-btn" onClick={() => setSaving(false)}>✕</button>
                        </div>
                    ) : (
                        <button
                            className={`saved-save-btn ${justSaved ? 'saved-just-saved' : ''}`}
                            onClick={() => setSaving(true)}
                        >
                            {justSaved ? '✓ Saved!' : '+ Save this commute'}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}
