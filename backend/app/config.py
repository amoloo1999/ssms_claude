from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "SQL Studio"
    secret_key: str = "change-me-in-production-use-a-real-secret-key"
    database_url: str = "sqlite+aiosqlite:///./sql_studio.db"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/callback"
    allowed_domain: str = ""  # e.g. "yourcompany.com" — empty allows all

    # Frontend URL for CORS and redirects
    frontend_url: str = "http://localhost:5173"

    # Config file path for seeding servers
    config_file: str = "config.yaml"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
