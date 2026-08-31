"""
SafeMAPS AI agent orchestration.

The primary path uses Anthropic tool calling with tools discovered from the
SafeMAPS MCP server. A deterministic fallback is provided for local demos when
ANTHROPIC_API_KEY is not configured; it still invokes SafeMAPS through MCP.
"""

import json
import re
from typing import Any, AsyncIterator

import httpx

from config import settings
from .mcp_client import SafeMapsMCPClient, tools_for_anthropic
from .prompts import SYSTEM_PROMPT


LOCALITIES = {
    "koramangala": (12.9352, 77.6245),
    "whitefield": (12.9698, 77.7500),
    "indiranagar": (12.9784, 77.6408),
    "electronic city": (12.8452, 77.6602),
    "mg road": (12.9759, 77.6069),
    "m g road": (12.9759, 77.6069),
    "silk board": (12.9170, 77.6230),
    "hebbal": (13.0358, 77.5970),
    "yelahanka": (13.1007, 77.5963),
    "airport": (13.1986, 77.7066),
    "kengeri": (12.9081, 77.4855),
    "bommasandra": (12.8168, 77.6972),
}


def _event(event_type: str, **payload) -> dict[str, Any]:
    return {"type": event_type, **payload}


def _tools_for_openrouter(tools) -> list[dict[str, Any]]:
    """OpenRouter (and any OpenAI-compatible endpoint) expects tools as
    {"type": "function", "function": {name, description, parameters}} —
    reshaped from the same MCP tool objects `tools_for_anthropic` uses."""
    anthropic_shaped = tools_for_anthropic(tools)
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in anthropic_shaped
    ]


def _find_place(message: str, default: str | None = None) -> tuple[str, tuple[float, float]] | None:
    text = message.lower()
    matches = [(name, coords) for name, coords in LOCALITIES.items() if name in text]
    if matches:
        return max(matches, key=lambda item: len(item[0]))
    if default and default in LOCALITIES:
        return default, LOCALITIES[default]
    return None


def _find_two_places(message: str) -> tuple[tuple[str, tuple[float, float]], tuple[str, tuple[float, float]]] | None:
    text = message.lower()
    matches = []
    for name, coords in LOCALITIES.items():
        idx = text.find(name)
        if idx >= 0:
            matches.append((idx, name, coords))
    matches.sort()
    deduped = []
    seen = set()
    for _idx, name, coords in matches:
        if coords not in seen:
            deduped.append((name, coords))
            seen.add(coords)
    if len(deduped) >= 2:
        return deduped[0], deduped[1]
    return None


def _tool_summary(tool: str, result: Any) -> str:
    if isinstance(result, dict) and result.get("error"):
        return f"{tool} returned an error: {result['error']}"
    if tool == "compare_route_profiles" and isinstance(result, dict):
        routes = result.get("routes", [])
        if not routes:
            return "SafeMAPS could not calculate comparable routes for those points."
        fastest = min(routes, key=lambda r: r.get("travel_time_minutes", 10**9))
        safest = next((r for r in routes if r.get("profile") == "safest"), routes[0])
        delta = safest.get("travel_time_minutes", 0) - fastest.get("travel_time_minutes", 0)
        return (
            f"SafeMAPS compared {len(routes)} route profiles. The fastest route is "
            f"{fastest.get('travel_time_minutes')} min over {fastest.get('distance_km')} km. "
            f"The safest route is {safest.get('travel_time_minutes')} min over "
            f"{safest.get('distance_km')} km, about {delta:.1f} min different, with "
            f"{safest.get('accident_hotspots_passed')} modeled accident hotspot(s) passed. "
            "Treat these modeled risk scores as planning signals, not safety guarantees."
        )
    if tool == "get_safe_route" and isinstance(result, dict):
        return (
            f"The {result.get('profile')} route is about "
            f"{result.get('travel_time_minutes')} minutes over {result.get('distance_km')} km, "
            f"with average AQI {result.get('avg_aqi')} and "
            f"{result.get('accident_hotspots_passed')} modeled accident hotspot(s) passed."
        )
    if tool == "get_aqi_near" and isinstance(result, dict):
        return f"The best available AQI estimate nearby is {result.get('aqi')} from {result.get('source')}."
    if tool == "get_accident_risk_near" and isinstance(result, dict):
        return (
            f"SafeMAPS found {result.get('count', 0)} nearby BTP/OpenCity accident-risk "
            f"blackspot(s), with {result.get('total_fatal', 0)} historical fatal crashes "
            "represented in that area."
        )
    return "SafeMAPS completed the requested MCP tool call."


class SafeMapsAgent:
    def __init__(self):
        self.mcp = SafeMapsMCPClient(
            settings.mcp_server_url,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )

    async def stream(
        self,
        message: str,
        session_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """`history` is prior turns for this session (PRD §25 follow-up
        support) and is mutated in place — the caller persists it back to
        the session store after the stream completes."""
        yield _event("assistant_start", session_id=session_id)
        yield _event("status", message="Connecting to SafeMAPS MCP...")
        tools = await self.mcp.list_tools()
        yield _event("mcp_status", status="connected", tool_count=len(tools))

        if settings.llm_provider == "openrouter" and settings.openrouter_api_key:
            async for item in self._stream_openrouter(message, tools, history if history is not None else []):
                yield item
        elif settings.anthropic_api_key:
            async for item in self._stream_anthropic(message, tools, history if history is not None else []):
                yield item
        else:
            async for item in self._stream_fallback(message):
                yield item

        yield _event("done")

    async def _stream_anthropic(
        self, message: str, tools, history: list[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        messages = history  # mutated in place; caller persists it to the session store
        messages.append({"role": "user", "content": message})
        tool_defs = tools_for_anthropic(tools)

        for _iteration in range(settings.ai_max_tool_iterations):
            response = await self._anthropic_messages(messages, tool_defs)
            content = response.get("content", [])
            messages.append({"role": "assistant", "content": content})

            tool_uses = [block for block in content if block.get("type") == "tool_use"]
            text_parts = [block.get("text", "") for block in content if block.get("type") == "text"]
            if text_parts and not tool_uses:
                yield _event("text_delta", content="\n".join(text_parts))
                return

            if not tool_uses:
                yield _event("text_delta", content="\n".join(text_parts) or "I could not complete that request.")
                return

            tool_results = []
            for tool_use in tool_uses:
                name = tool_use["name"]
                args = tool_use.get("input", {})
                yield _event("tool_start", tool=name, arguments=args)
                call = await self.mcp.call_tool(name, args)
                yield _event(
                    "tool_result",
                    tool=name,
                    arguments=args,
                    duration_ms=call.duration_ms,
                    result=call.result,
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": json.dumps(call.result, default=str),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        yield _event("error", message="The agent reached the maximum tool-iteration limit.")

    async def _anthropic_messages(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.ai_request_timeout_seconds) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key or "",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": 900,
                    "system": SYSTEM_PROMPT,
                    "messages": messages,
                    "tools": tools,
                },
            )
        response.raise_for_status()
        return response.json()

    # ── OpenRouter path ──────────────────────────────────────────────
    # OpenRouter speaks the OpenAI chat-completions + `tool_calls` shape,
    # not Anthropic's native content-block/`tool_use` shape used above —
    # so this keeps its own message history format (role/content/
    # tool_calls, plus role:"tool" for results) rather than sharing
    # `_stream_anthropic`'s. Which shape a session's history is in
    # follows whichever provider is configured server-side.

    async def _stream_openrouter(
        self, message: str, tools, history: list[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        messages = history  # mutated in place; caller persists it to the session store
        messages.append({"role": "user", "content": message})
        tool_defs = _tools_for_openrouter(tools)

        for _iteration in range(settings.ai_max_tool_iterations):
            try:
                response = await self._openrouter_chat(messages, tool_defs)
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:300]
                yield _event("error", message=f"OpenRouter request failed: {detail}")
                return

            choice = (response.get("choices") or [{}])[0]
            msg = choice.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            text = msg.get("content") or ""

            # Persist exactly what the API returned (minus provider-internal
            # fields) so the next turn's request is well-formed.
            messages.append({
                "role": "assistant",
                "content": msg.get("content"),
                **({"tool_calls": tool_calls} if tool_calls else {}),
            })

            if not tool_calls:
                yield _event("text_delta", content=text or "I could not complete that request.")
                return

            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield _event("tool_start", tool=name, arguments=args)
                result = await self.mcp.call_tool(name, args)
                yield _event(
                    "tool_result",
                    tool=name,
                    arguments=args,
                    duration_ms=result.duration_ms,
                    result=result.result,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result.result, default=str),
                })

        yield _event("error", message="The agent reached the maximum tool-iteration limit.")

    async def _openrouter_chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key or ''}",
            "content-type": "application/json",
        }
        if settings.openrouter_site_url:
            headers["HTTP-Referer"] = settings.openrouter_site_url
        if settings.openrouter_app_name:
            headers["X-Title"] = settings.openrouter_app_name

        async with httpx.AsyncClient(timeout=settings.ai_request_timeout_seconds) as client:
            response = await client.post(
                f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": settings.openrouter_model,
                    "max_tokens": 900,
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
                    "tools": tools,
                },
            )
        response.raise_for_status()
        return response.json()

    async def _stream_fallback(self, message: str) -> AsyncIterator[dict[str, Any]]:
        text = message.lower()
        places = _find_two_places(text)

        if places and ("compare" in text or "vs" in text or "fastest" in text and "safest" in text):
            origin, dest = places
            args = {
                "origin_lat": origin[1][0],
                "origin_lon": origin[1][1],
                "dest_lat": dest[1][0],
                "dest_lon": dest[1][1],
            }
            async for item in self._fallback_tool("compare_route_profiles", args):
                yield item
            return

        if places and re.search(r"\b(route|safest|fastest|healthiest|balanced)\b", text):
            origin, dest = places
            profile = "balanced"
            for candidate in ("safest", "fastest", "healthiest"):
                if candidate in text:
                    profile = candidate
                    break
            args = {
                "origin_lat": origin[1][0],
                "origin_lon": origin[1][1],
                "dest_lat": dest[1][0],
                "dest_lon": dest[1][1],
                "profile": profile,
            }
            async for item in self._fallback_tool("get_safe_route", args):
                yield item
            return

        place = _find_place(text)
        if place and "aqi" in text:
            args = {"lat": place[1][0], "lon": place[1][1], "radius_m": 1500}
            async for item in self._fallback_tool("get_aqi_near", args):
                yield item
            return

        if place and ("accident" in text or "risk" in text or "safety" in text):
            args = {"lat": place[1][0], "lon": place[1][1], "radius_m": 1500}
            async for item in self._fallback_tool("get_accident_risk_near", args):
                yield item
            return

        yield _event(
            "text_delta",
            content=(
                "I can help with SafeMAPS route, AQI, and accident-risk questions. "
                "Try: Compare safest vs fastest route from Koramangala to Whitefield."
            ),
        )

    async def _fallback_tool(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        yield _event("status", message=f"Calling {tool}...")
        yield _event("tool_start", tool=tool, arguments=arguments)
        call = await self.mcp.call_tool(tool, arguments)
        yield _event(
            "tool_result",
            tool=tool,
            arguments=arguments,
            duration_ms=call.duration_ms,
            result=call.result,
        )
        yield _event("text_delta", content=_tool_summary(tool, call.result))
