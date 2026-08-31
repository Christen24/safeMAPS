"""
In-memory conversation history for the SafeMAPS AI Demo (PRD §25).

Deliberately short-lived: no database, no accounts. Just enough state for
a follow-up question ("what about the fastest one?") to retain the
origin/destination from the previous turn. Sessions expire after
inactivity and are capped in count to bound memory on a public instance.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

SESSION_TTL_S = 30 * 60          # 30 minutes of inactivity
MAX_SESSIONS = 500               # hard cap — oldest evicted first
MAX_MESSAGES_PER_SESSION = 40    # ~20 user turns before we trim history


@dataclass
class _Session:
    messages: list[dict] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, _Session] = {}

    def get(self, session_id: str) -> list[dict]:
        self._evict_expired()
        session = self._sessions.get(session_id)
        if session is None:
            return []
        session.last_used = time.monotonic()
        return session.messages

    def set(self, session_id: str, messages: list[dict]) -> None:
        self._evict_expired()
        session = self._sessions.get(session_id)
        if session is None:
            if len(self._sessions) >= MAX_SESSIONS:
                self._evict_oldest()
            session = _Session()
            self._sessions[session_id] = session
        if len(messages) > MAX_MESSAGES_PER_SESSION:
            messages = messages[-MAX_MESSAGES_PER_SESSION:]
        session.messages = messages
        session.last_used = time.monotonic()

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [sid for sid, s in self._sessions.items() if now - s.last_used > SESSION_TTL_S]
        for sid in expired:
            self._sessions.pop(sid, None)

    def _evict_oldest(self) -> None:
        if not self._sessions:
            return
        oldest_id = min(self._sessions, key=lambda sid: self._sessions[sid].last_used)
        self._sessions.pop(oldest_id, None)


session_store = SessionStore()
