import os
import asyncio
import sys

# Add root folder to python path so we can import dependencies if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import asyncpg
except ImportError:
    print("Error: asyncpg is required to run migrations. Install it via 'pip install asyncpg'")
    sys.exit(1)

# Simple parser to load .env manually if python-dotenv is not installed
def load_env_fallback():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip()
                    if key not in os.environ:
                        os.environ[key] = val

load_env_fallback()

async def run_sql_file(conn, file_path):
    print(f"Applying: {os.path.basename(file_path)}")
    if not os.path.exists(file_path):
        print(f"  ❌ Error: File not found: {file_path}")
        sys.exit(1)
    with open(file_path, "r", encoding="utf-8") as f:
        sql = f.read()
    
    # Execute SQL
    await conn.execute(sql)
    print(f"  ✓ {os.path.basename(file_path)} applied")

async def main():
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = int(os.getenv("POSTGRES_PORT", "5433"))
    database = os.getenv("POSTGRES_DB", "healthroute")
    user = os.getenv("POSTGRES_USER", "healthroute")
    password = os.getenv("POSTGRES_PASSWORD", "changeme_in_production")

    print(f"Connecting to database at {host}:{port}/{database} as {user}...")
    try:
        conn = await asyncpg.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password
        )
        print("Connected successfully.")
    except Exception as e:
        print(f"Failed to connect to database: {e}")
        print("Please check your .env settings and ensure the database is running.")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    migrations = [
        "database_seeder.sql",
        "migration_phase5.sql",
        "migration_phase6.sql",
        "migration_cpcb.sql",
        "migration_incidents.sql"
    ]

    for m in migrations:
        await run_sql_file(conn, os.path.join(script_dir, m))

    await conn.close()
    print("\nAll migrations completed successfully.")

if __name__ == "__main__":
    asyncio.run(main())
