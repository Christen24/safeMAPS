# PRD: SafeMAPS MCP Server

**Status:** In progress — core tools built, transport/deployment not started
**Owner:** Chris
**Last updated:** Aug 1, 2026

---

## 1. Problem / Opportunity

SafeMAPS already computes health-and-safety-aware routes across Bangalore
(weighted A*, travel time + AQI exposure + accident risk), backed by real
data (OSM road network, CPCB/WAQI AQI, real BTP accident blackspots via
OpenCity). Today the only way to use it is the web app.

MCP turns the same logic into something Claude (Desktop, claude.ai, or any
MCP client) can call directly and reason about conversationally — "what's
the safest way from Koramangala to Whitefield right now, and why is it
slower than the fastest route?" — without anyone touching the UI.

This is also the single most resume-visible piece of the project: a
recruiter can connect to a live URL and query it from their own Claude
Desktop, rather than trusting a screenshot or a video.

## 2. Goals

- Expose SafeMAPS' routing, AQI, and accident-risk logic as MCP tools with
  zero duplicated business logic (tools call the existing async functions
  directly — no new business logic, no re-implementing the FastAPI routes).
- Make it reachable remotely (not just localhost + Claude Desktop config),
  so it's actually demoable without cloning the repo.
- Keep it read-only. No tool in this server should mutate state — this
  keeps the trust/safety surface small and avoids write-confirmation UX
  entirely.

### Non-goals (explicitly out of scope for v1)
- Auth / per-user rate limiting on the MCP server (fine for a portfolio
  demo at low traffic; flag as a known gap, don't build it now)
- Write tools (e.g. "report a new hazard") — different trust model,
  separate PRD if pursued later
- Multi-city support — Bangalore-only, same as the rest of SafeMAPS

## 3. Current State (as of this doc)

`backend/mcp_server.py` already exists and already implements the full
tool surface originally scoped. This section exists so future-you (or
anyone picking this up) doesn't re-plan work that's done.

| Tool | Backs onto | Status |
|---|---|---|
| `get_safe_route(origin, dest, profile?, departure_time?)` | `routing.find_route()` | ✅ Done |
| `compare_route_profiles(origin, dest, departure_time?)` | 4× `find_route()` via `asyncio.gather` | ✅ Done |
| `explain_route_cost(origin, dest, profile?)` | `find_route()` + plain-language α/β/γ breakdown | ✅ Done |
| `get_aqi_heatmap_summary(bbox)` | `spatial_queries.get_aqi_heatmap()` | ✅ Done |
| `get_aqi_near(lat, lon, radius?)` | grid cell, falls back to nearest station | ✅ Done |
| `get_accident_risk_near(lat, lon, radius?)` | `spatial_queries.get_blackspots_in_bbox()` | ✅ Done, now backed by real BTP data |

Architecture decisions already made and coded:
- Tools import and call FastAPI's own async functions directly
  (`from routing import find_route`) — no internal HTTP hop.
- The MCP process loads its **own** graph cache + DB pool at startup,
  separate from the FastAPI app's (not sharing a process).
- Transport: `mcp.run()` with no transport arg → **stdio only**. Works
  with Claude Desktop's local config. Not reachable from claude.ai or
  by anyone who hasn't cloned the repo.

## 4. Gaps / Remaining Work

### 4.1 Transport (blocking — nothing below matters until this is done)
Switch from stdio to streamable-http (or SSE) transport so the server
can run as a long-lived process with a URL instead of a subprocess
Claude Desktop spawns locally.

- **Startup cost matters for hosting choice**: graph_cache.load() takes
  a few seconds and ~200MB of memory. Free-tier hosts that spin down
  idle instances (e.g. Render free tier) will pay that cost on every
  cold start, which is a bad demo experience if a recruiter's first
  request times out. Fly.io / Railway with a small always-on instance
  avoids this.

### 4.2 Deployment
- Dockerfile for the MCP process (can likely reuse most of the existing
  backend Dockerfile — same deps, different entrypoint).
- Deploy to Railway or Fly.io.
- Point it at the same Postgres/PostGIS instance the main backend uses
  (or a read replica, if we want to be careful about the MCP process
  competing with the web app for connections under load — unlikely to
  matter at demo traffic, worth a one-line note not a redesign).

### 4.3 New tool: AQI forecasting
`GET /aqi/predict` already exists in the FastAPI backend (LSTM-based,
reads from an `aqi_predictions` table with an on-the-fly inference
fallback) but isn't exposed via MCP. This wasn't in the original tool
list and is worth adding — "what will AQI look like on my route in 30
minutes" is a stronger differentiator than anything currently exposed,
since it's the one tool that reasons about the future rather than just
querying current state.

Proposed signature: `predict_aqi_near(lat, lon, minutes_ahead=30)`,
thin wrapper around the existing `predict_aqi()` route logic.

### 4.4 Docs
- README section: "Connect to Claude" — server URL, transport type, and
  2–3 example prompts a recruiter could paste into Claude Desktop
  verbatim (e.g. "Compare the fastest and safest route from MG Road to
  Electronic City right now").
- Note the read-only guarantee explicitly in the README — worth stating
  outright since it's a real trust signal, not just an implementation
  detail.

## 5. Build Structure

```
backend/
  mcp_server.py          # existing — tool definitions (done)
  Dockerfile.mcp          # NEW — separate from Dockerfile.backend if the
                           #   entrypoint/transport config differs enough
                           #   to not share one image cleanly; otherwise
                           #   just a different CMD in the same image
  routes/aqi.py            # existing — predict_aqi() to be reused, not rewritten
infrastructure/
  railway.toml / fly.toml  # NEW — deploy config, whichever host is picked
README.md                  # UPDATE — "Connect to Claude" section
docs/
  mcp_setup.md              # NEW (optional) — local stdio setup instructions,
                             #   separate from the remote-URL instructions in
                             #   the main README, since local dev (cloning +
                             #   running against your own DB) and "just try
                             #   the deployed one" are different audiences
```

## 6. Milestones

| # | Milestone | Depends on |
|---|---|---|
| 1 | Switch transport to streamable-http, verify all 6 existing tools respond over HTTP locally | — |
| 2 | Add `predict_aqi_near` tool | Reuses existing `predict_aqi()` logic; independent of transport work, can happen in parallel |
| 3 | Dockerfile + deploy to Railway/Fly.io, get stable URL | 1 |
| 4 | README "Connect to Claude" section + example prompts | 3 |
| 5 | (Stretch) Smoke-test from an actual second machine's Claude Desktop, not just localhost | 3, 4 |

## 7. Risks / Open Questions

- **Cold start UX**: if the chosen host spins down on idle, first query
  after a while could time out or feel slow. Decide always-on vs
  spin-down before picking a host, not after deploying.
- **DB connection contention**: MCP process + FastAPI app both hold
  their own pool against the same Postgres instance. Fine at demo
  scale; worth a one-line README caveat rather than solving now.
- **No rate limiting**: a public URL with no auth means anyone who finds
  it can query it freely. Acceptable for a portfolio piece at expected
  traffic; would need revisiting before any real-world usage claim.

## 8. Success Criteria

- A person with no local setup can add the deployed URL to their own
  Claude Desktop config and get a working `get_safe_route` response
  within one retry.
- All 7 tools (6 existing + `predict_aqi_near`) return correctly-typed
  results against real Bangalore data, not stub/mock data.
- README's "Connect to Claude" section is accurate enough that a
  recruiter following it hits no dead ends.
