# SafeMAPS MCP Server

`backend/mcp_server.py` exposes SafeMAPS's routing, AQI, and accident-risk
logic as MCP tools, so Claude can be asked things like:

> "Find me the safest route from Koramangala to Whitefield, and explain why it's safer than the fastest one."

It is a thin wrapper — every tool calls directly into the same functions
the FastAPI app uses (`routing.find_route`, `spatial_queries.get_aqi_heatmap`,
etc). No routing/AQI logic is duplicated.

## Tools

| Tool | Backs onto |
|---|---|
| `get_safe_route` | `routing.find_route` |
| `compare_route_profiles` | same 4-profile comparison as `GET /route/compare` |
| `explain_route_cost` | `find_route` + a plain-language cost breakdown |
| `get_aqi_heatmap_summary` | `spatial_queries.get_aqi_heatmap` (summarized — see note) |
| `get_aqi_near` | nearest grid cell, falling back to nearest station |
| `get_accident_risk_near` | `spatial_queries.get_blackspots_in_bbox` |

Note: `get_aqi_heatmap_summary` returns stats + the 5 worst cells rather
than the full grid GeoJSON — hundreds of raw cells aren't useful for an
LLM to reason over. The map UI still uses the full `/api/aqi/heatmap`
endpoint for rendering.

## Running locally (stdio — Claude Desktop)

```bash
cd backend
pip install -r requirements.txt
python mcp_server.py
```

This loads the same in-memory road graph the FastAPI app uses at startup
(~200MB, a few seconds), so make sure Postgres/PostGIS is running and
`.env` is configured first (same `.env` as the FastAPI backend).

Add it to Claude Desktop's config:

```json
{
  "mcpServers": {
    "safemaps": {
      "command": "python",
      "args": ["/absolute/path/to/backend/mcp_server.py"]
    }
  }
}
```

## Going remote (resume-visible deployment)

For a demo anyone can plug into Claude without cloning the repo, swap the
stdio transport for `streamable-http`/SSE and deploy it (Railway/Render/Fly.io):

```python
mcp.run(transport="streamable-http")
```

Then add the server URL to the top-level README's "Connect to Claude"
section. Two things worth deciding before doing this:

- **DB pool**: give the deployed MCP process its own read-only Postgres
  connection (don't share a pool object across processes/hosts).
- **Startup cost**: the ~200MB graph load takes a few seconds — fine for
  a long-running server, but factor it into whatever health check /
  cold-start behavior your host expects.
