const API_BASE = '/api';

export async function streamAIChat({ message, sessionId, onEvent, signal }) {
    const response = await fetch(`${API_BASE}/ai/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, session_id: sessionId }),
        signal,
    });

    if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'SafeMAPS AI is unavailable.');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';

        for (const frame of frames) {
            const line = frame.split('\n').find(part => part.startsWith('data: '));
            if (!line) continue;
            try {
                onEvent(JSON.parse(line.slice(6)));
            } catch (error) {
                console.warn('Invalid AI stream event:', error);
            }
        }
    }
}

export async function getAIStatus() {
    const response = await fetch(`${API_BASE}/ai/status`);
    if (!response.ok) throw new Error('AI status unavailable');
    return response.json();
}

export async function getProfileRoutes(origin_lat, origin_lon, dest_lat, dest_lon) {
    const response = await fetch(`${API_BASE}/ai/profile-routes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ origin_lat, origin_lon, dest_lat, dest_lon }),
    });
    if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'Could not fetch route profiles.');
    }
    return response.json();
}
