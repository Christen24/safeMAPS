"""
SafeMAPS Backend Configuration
Reads settings from environment variables / .env file.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # --- Database ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "healthroute"
    postgres_user: str = "healthroute"
    postgres_password: str = "changeme_in_production"

    # --- API Keys ---
    waqi_api_token: Optional[str] = None
    tomtom_api_key: Optional[str] = None

    # --- Server ---
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    # --- Bangalore Bounding Box ---
    bbox_min_lat: float = 12.85
    bbox_max_lat: float = 13.15
    bbox_min_lon: float = 77.45
    bbox_max_lon: float = 77.78

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
