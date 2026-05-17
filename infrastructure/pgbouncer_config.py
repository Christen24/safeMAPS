"""
SafeMAPS — PgBouncer Configuration Reference

PgBouncer sits between the FastAPI backend (asyncpg) and PostGIS, acting as
a connection pooler. This prevents connection exhaustion under load.

Problem:
  /compare runs 4 concurrent A* searches, each snapping 2 nodes = 8 DB
  connections per request. Under 50 concurrent users that's 400 connections,
  which exceeds Postgres's default limit of 100.

Solution:
  PgBouncer transaction-mode pooling with pool_size=20 connections per DB.
  Multiple backend connections share those 20 physical connections, capped
  by asyncpg's pool settings.

Docker integration:
  The pgbouncer service in docker-compose.yml intercepts port 5433.
  Backend DATABASE_URL points to pgbouncer:5433 instead of postgis:5432.

File: infrastructure/pgbouncer.ini
Generated automatically by docker-compose from environment variables.
"""

PGBOUNCER_INI_TEMPLATE = """
[databases]
{postgres_db} = host={postgres_host} port=5432 dbname={postgres_db}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 5433
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt

; Transaction pooling — safest for asyncpg prepared statements
pool_mode = transaction

; Connections to Postgres (physical)
max_client_conn = 500
default_pool_size = 20
min_pool_size = 2
reserve_pool_size = 5

; Timeouts
connect_timeout = 10
server_idle_timeout = 600
client_idle_timeout = 300

; Logging
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1
"""

USERLIST_TEMPLATE = '"{user}" "{password}"\n'


def generate_pgbouncer_config(
    postgres_host: str,
    postgres_db: str,
    postgres_user: str,
    postgres_password: str,
) -> tuple[str, str]:
    """
    Returns (pgbouncer.ini content, userlist.txt content).
    Called by the Docker entrypoint script to generate config from env vars.
    """
    ini = PGBOUNCER_INI_TEMPLATE.format(
        postgres_host=postgres_host,
        postgres_db=postgres_db,
    )
    userlist = USERLIST_TEMPLATE.format(
        user=postgres_user,
        password=postgres_password,
    )
    return ini.strip(), userlist.strip()
