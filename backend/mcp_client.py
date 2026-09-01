import os
import httpx
import logging

logger = logging.getLogger(__name__)

class SafeMapsMCPClient:
    def __init__(self):
        self.mcp_url = os.getenv("MCP_SERVER_URL", "http://mcp:8001/mcp")
        self.mcp_base = self.mcp_url[:-4] if self.mcp_url.endswith("/mcp") else self.mcp_url
        self.client = httpx.AsyncClient(timeout=60.0)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        url = f"{self.mcp_base}/internal/{name}"
        res = await self.client.post(url, json=arguments)
        res.raise_for_status()
        return res.json()

    async def close(self):
        await self.client.aclose()

mcp_client = SafeMapsMCPClient()
