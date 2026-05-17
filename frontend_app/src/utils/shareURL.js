/**
 * SafeMAPS — URL Share Utilities
 *
 * Encodes/decodes route parameters into URL query params so routes
 * can be shared or bookmarked.
 *
 * URL format:
 *   ?from=12.9716,77.5946&to=12.9352,77.6101&profile=healthiest&t=08:30
 *
 * Usage:
 *   encodeRouteToURL(origin, destination, profile, departureTime)
 *   → sets window.location.search
 *
 *   decodeURLToRoute()
 *   → { origin, destination, profile, departureTime } | null
 */

export function encodeRouteToURL(origin, destination, profile, departureTime) {
    if (!origin?.lat || !destination?.lat) return;
    const params = new URLSearchParams();
    params.set('from', `${(+origin.lat).toFixed(6)},${(+origin.lon).toFixed(6)}`);
    params.set('to',   `${(+destination.lat).toFixed(6)},${(+destination.lon).toFixed(6)}`);
    if (profile) params.set('profile', profile);
    if (departureTime) params.set('t', departureTime);
    const newURL = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState({}, '', newURL);
}

export function decodeURLToRoute() {
    const params = new URLSearchParams(window.location.search);
    const fromStr    = params.get('from');
    const toStr      = params.get('to');
    const profile    = params.get('profile');
    const departure  = params.get('t');

    if (!fromStr || !toStr) return null;

    const [fromLat, fromLon] = fromStr.split(',').map(Number);
    const [toLat,   toLon]   = toStr.split(',').map(Number);

    if (isNaN(fromLat) || isNaN(fromLon) || isNaN(toLat) || isNaN(toLon)) return null;
    if (fromLat < -90 || fromLat > 90 || toLat < -90 || toLat > 90) return null;

    return {
        origin:        { lat: String(fromLat), lon: String(fromLon) },
        destination:   { lat: String(toLat),   lon: String(toLon)   },
        profile:       profile || 'balanced',
        departureTime: departure || null,
    };
}

export function clearURLParams() {
    window.history.replaceState({}, '', window.location.pathname);
}

export function buildShareURL(origin, destination, profile, departureTime) {
    const params = new URLSearchParams();
    params.set('from', `${(+origin.lat).toFixed(6)},${(+origin.lon).toFixed(6)}`);
    params.set('to',   `${(+destination.lat).toFixed(6)},${(+destination.lon).toFixed(6)}`);
    if (profile) params.set('profile', profile);
    if (departureTime) params.set('t', departureTime);
    return `${window.location.origin}${window.location.pathname}?${params.toString()}`;
}
