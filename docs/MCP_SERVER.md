# SafeMAPS MCP Server

> **This file is superseded.** See [`docs/mcp_setup.md`](mcp_setup.md) for
> current setup instructions, the full 7-tool list, and example prompts.
>
> This doc originally described an early stdio-transport version of the
> server (`{"command": "python", "args": [...]}` in Claude Desktop's
> config). The server has since moved to `streamable-http` exclusively —
> it runs as a long-lived HTTP process (`python mcp_server.py`, serving
> `/mcp` and `/health` on port 8001) and is connected to via a URL, not a
> spawned subprocess. The old stdio config in this file no longer applies
> and won't work against the current server. Kept as a stub rather than
> deleted so any existing links to this path don't 404 — please update
> them to point at `mcp_setup.md` instead.
