"""Public SafeMAPS AI + MCP demo endpoints."""

import json
import time
import uuid
from collections import defaultdict, deque
from typing import Deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai.agent import SafeMapsAgent
from ai.session import session_store
from config import settings

router = APIRouter()

_requests_by_ip: dict[str, Deque[float]] = defaultdict(deque)
_requests_by_ip_day: dict[str, Deque[float]] = defaultdict(deque)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(ip: str) -> None:
    now = time.monotonic()

    window = settings.ai_rate_limit_window_seconds
    bucket = _requests_by_ip[ip]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= settings.ai_rate_limit_requests:
        raise HTTPException(
            status_code=429,
            detail="Too many AI demo requests. Please wait a few minutes and try again.",
        )

    day_window = settings.ai_rate_limit_day_seconds
    day_bucket = _requests_by_ip_day[ip]
    while day_bucket and now - day_bucket[0] > day_window:
        day_bucket.popleft()
    if len(day_bucket) >= settings.ai_rate_limit_day_requests:
        raise HTTPException(
            status_code=429,
            detail="Daily limit reached for this demo. Please try again tomorrow.",
        )

    bucket.append(now)
    day_bucket.append(now)


@router.get("/status")
async def ai_status():
    agent = SafeMapsAgent()
    try:
        tools = await agent.mcp.list_tools()
        return {
            "mcp": "connected",
            "tool_count": len(tools),
            "tools": [tool.name for tool in tools],
            "llm": "anthropic" if settings.anthropic_api_key else "local-demo-fallback",
        }
    except Exception as exc:
        return {
            "mcp": "unavailable",
            "tool_count": 0,
            "tools": [],
            "llm": "anthropic" if settings.anthropic_api_key else "local-demo-fallback",
            "error": str(exc),
        }


@router.post("/chat")
async def ai_chat(payload: ChatRequest, request: Request):
    if len(payload.message) > settings.ai_max_message_length:
        raise HTTPException(
            status_code=413,
            detail=f"Message is too long. Limit: {settings.ai_max_message_length} characters.",
        )

    _enforce_rate_limit(_client_ip(request))
    session_id = payload.session_id or str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    async def stream():
        yield _sse({"type": "request_start", "request_id": request_id, "session_id": session_id})
        history = session_store.get(session_id)
        try:
            agent = SafeMapsAgent()
            async for event in agent.stream(payload.message, session_id=session_id, history=history):
                yield _sse({"request_id": request_id, **event})
            session_store.set(session_id, history)
        except Exception as exc:
            yield _sse(
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "SafeMAPS AI could not complete the request.",
                    "detail": str(exc),
                }
            )
            yield _sse({"type": "done", "request_id": request_id})

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/reset")
async def ai_reset(payload: dict):
    session_id = payload.get("session_id")
    if session_id:
        session_store.reset(session_id)
    return {"status": "reset"}
