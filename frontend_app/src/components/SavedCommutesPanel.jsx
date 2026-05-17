/**
 * SafeMAPS — Saved Commutes Panel Component
 *
 * Displays up to 5 saved commutes in the sidebar.
 * One click loads the commute and triggers route computation.
 * "Save current" button appears when a route has been computed
 * and the origin/destination pair is not already saved.
 *
 * Props:
 *   origin, destination, profile  — current route inputs
 *   onLoad(origin, dest, profile) — callback to fill inputs + compute
 *   isActive                      — true when a route is showing
 */

import { useState, useEffect, useCallback } from 'react';
import {
    getSavedCommutes,
    saveCommute,
    deleteCommute,
    commuteExists,
} from '../utils/savedCommutes';

const PROFILE_ICONS = {
    fastest:    '⚡',
    safest:     '🛡️',
    healthiest: '🫁',
    balanced:   '⚖️',
};

export default function SavedCommutesPanel({ origin, destination, profile, onLoad, isActive }) {
    const [commutes,   setCommutes]   = useState([]);
    const [saveName,   setSaveName]   = useState('');
    const [saving,     setSaving]     = useState(false);
    const [justSaved,  setJustSaved]  = useState(false);

    // Reload from localStorage whenever props change or panel opens
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
            {/* Header row */}
            <div className="saved-panel-header">
                <span className="saved-panel-title">Saved Commutes</span>
                <span className="saved-panel-count">{commutes.length}/5</span>
            </div>

            {/* Commute list */}
            {commutes.length === 0 ? (
                <p className="saved-empty">No saved commutes yet. Compute a route and save it.</p>
            ) : (
                <div className="saved-list">
                    {commutes.map(c => (
                        <div
                            key={c.id}
                            className="saved-item"
                            onClick={() => handleLoad(c)}
                            role="button"
                            tabIndex={0}
                            onKeyDown={e => e.key === 'Enter' && handleLoad(c)}
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
                                </div>
                            </div>
                            <button
                                className="saved-delete-btn"
                                onClick={e => handleDelete(c.id, e)}
                                title="Delete commute"
                                aria-label="Delete"
                            >
                                ✕
                            </button>
                        </div>
                    ))}
                </div>
            )}

            {/* Save current route */}
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
