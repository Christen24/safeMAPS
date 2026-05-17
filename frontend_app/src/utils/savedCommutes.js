/**
 * SafeMAPS — Saved Commutes Storage Utility
 *
 * Persists up to MAX_SAVED commutes in localStorage.
 * Each commute: { id, name, origin, destination, profile, savedAt }
 *
 * Usage:
 *   getSavedCommutes()        → array of commutes
 *   saveCommute(name, origin, destination, profile) → id
 *   deleteCommute(id)         → void
 *   renameCommute(id, name)   → void
 */

const KEY       = 'safemaps_commutes_v1';
const MAX_SAVED = 5;

export function getSavedCommutes() {
    try {
        const raw = localStorage.getItem(KEY);
        return raw ? JSON.parse(raw) : [];
    } catch {
        return [];
    }
}

function _save(commutes) {
    try {
        localStorage.setItem(KEY, JSON.stringify(commutes));
    } catch {
        console.warn('localStorage full — cannot save commute');
    }
}

export function saveCommute(name, origin, destination, profile) {
    if (!origin?.lat || !destination?.lat) return null;
    const existing = getSavedCommutes();
    const id = `${Date.now()}`;
    const newCommute = {
        id,
        name:        name || `Commute ${existing.length + 1}`,
        origin:      { lat: String(origin.lat),      lon: String(origin.lon) },
        destination: { lat: String(destination.lat), lon: String(destination.lon) },
        profile:     profile || 'balanced',
        savedAt:     new Date().toISOString(),
    };
    const updated = [newCommute, ...existing].slice(0, MAX_SAVED);
    _save(updated);
    return id;
}

export function deleteCommute(id) {
    _save(getSavedCommutes().filter(c => c.id !== id));
}

export function renameCommute(id, name) {
    _save(getSavedCommutes().map(c => c.id === id ? { ...c, name } : c));
}

export function commuteExists(origin, destination) {
    if (!origin?.lat || !destination?.lat) return false;
    return getSavedCommutes().some(c =>
        Math.abs(+c.origin.lat      - +origin.lat)      < 0.0001 &&
        Math.abs(+c.origin.lon      - +origin.lon)      < 0.0001 &&
        Math.abs(+c.destination.lat - +destination.lat) < 0.0001 &&
        Math.abs(+c.destination.lon - +destination.lon) < 0.0001
    );
}
