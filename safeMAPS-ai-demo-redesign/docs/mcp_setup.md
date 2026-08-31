# SafeMAPS MCP Server — Setup Guide

The SafeMAPS MCP server exposes the same routing, AQI, and accident-risk
logic as the web app to Claude (Desktop, claude.ai, or any MCP client),
so you can query it conversationally without touching the UI.

**Transport:** `streamable-http` — runs as a long-lived HTTP process with
a stable URL. No subprocess spawning, reachable remotely.

---

## Option A — Use the Deployed Server (Recommended)

No local setup required. Add this to your Claude Desktop config
(`claude_desktop_config.json`, usually in `~/Library/Application Support/Claude/`
on macOS or `%APPDATA%\Claude\` on Windows):

```json
{
  "mcpServers": {
    "safemaps": {
      "url": "https://<your-railway-url>/mcp"
    }
  }
}
```

Replace `<your-railway-url>` with the deployed Railway URL once live.

---

## Option B — Run Locally Against Your Own DB

Requirements: Python 3.11+, a running Postgres/PostGIS instance loaded
with Bangalore OSM + BTP data (see the main README).

```bash
# From the repo root
cd backend
pip install -r requirements.txt

# Copy and fill in the .env (same vars as the FastAPI app)
cp ../.env.example ../.env

python mcp_server.py
# Server starts on http://0.0.0.0:8001
# Logs: "[safemaps-mcp] graph loaded: 559602 nodes"
```

Claude Desktop config for local:

```json
{
  "mcpServers": {
    "safemaps": {
      "url": "http://localhost:8001/mcp"
    }
  }
}
```

> **Note:** The MCP server loads its own in-memory road graph (~200MB,
> ~10–30s startup). This is separate from the FastAPI web app's process —
> both can run simultaneously without conflict.

---

## Available Tools (7 total)

| Tool | What it does |
|---|---|
| `get_safe_route` | Route from A→B with a named profile (fastest / safest / healthiest / balanced) |
| `compare_route_profiles` | All four profiles side-by-side with real trade-off numbers |
| `explain_route_cost` | Plain-language breakdown of why a route scored the way it did |
| `get_aqi_heatmap_summary` | AQI summary for a bounding box (avg/min/max + 5 worst cells) |
| `get_aqi_near` | Current AQI at a lat/lon (interpolated grid cell or nearest station) |
| `get_accident_risk_near` | Accident blackspots near a point (real BTP/OpenCity data) |
| `predict_aqi_near` | Forecasted AQI near a lat/lon N minutes from now (LSTM model) |

All tools are **read-only** — no tool mutates any state.

---

## Example Prompts

Paste these into Claude Desktop after connecting the server:

```
Compare the fastest and safest route from MG Road to Electronic City right now.
```

```
What is the current AQI near Silk Board Junction, and what will it be in 30 minutes?
```

```
Are there any recorded accident blackspots within 500m of Hebbal flyover?
```

```
Explain why the "healthiest" route from Koramangala to Whitefield costs more than the "fastest" one.
```

---

## Known Limits

- **No auth / rate limiting.** The deployed server is public. Fine for a
  portfolio demo; revisit before any production claim.
- **DB connection contention.** MCP process and FastAPI app both hold their
  own pool against the same Postgres instance. Fine at demo traffic.
- **Cold-start latency.** If the host spins down on idle, the first query
  after a long gap may be slow (~30s graph load). Use an always-on plan
  to avoid this for demos.
- **Bangalore only.** The road graph, AQI stations, and BTP blackspot data
  are all specific to Bangalore.
