"""
Async database connection pool using asyncpg.
"""

import asyncpg
from typing import Optional

from config import settings


class DatabaseUnavailable(RuntimeError):
    """
    Raised when a query is attempted before the pool is connected (or after
    startup gave up connecting). Distinct from a generic AttributeError so
    route handlers can catch it and return a clean 503 instead of a raw
    500 with a plain-text "Internal Server Error" body that hides the
    real cause from API clients.
    """


class Database:
    """Manages an async connection pool to PostGIS."""

    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Create the connection pool."""
        self.pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            min_size=2,
            max_size=10,
            command_timeout=60,
            statement_cache_size=0,
        )

    async def disconnect(self):
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise DatabaseUnavailable(
                "Database pool is not connected. Startup likely failed to "
                "reach Postgres/PgBouncer (check backend startup logs for "
                "'Startup attempt' / 'Startup failed' entries), or the pool "
                "was closed. Try POST /api/admin/refresh-graph once the DB "
                "is reachable, or restart the backend."
            )
        return self.pool

    async def fetch(self, query: str, *args):
        """Execute a query and return all rows."""
        async with self._require_pool().acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        """Execute a query and return a single row."""
        async with self._require_pool().acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        """Execute a query and return a single value."""
        async with self._require_pool().acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args):
        """Execute a query without returning results."""
        async with self._require_pool().acquire() as conn:
            return await conn.execute(query, *args)


db = Database()
