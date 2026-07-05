"""Application settings — the single source of runtime configuration.

Values come from environment variables (see ``infra/.env.template``). Business
logic never reads ``os.environ`` directly; it imports :data:`settings`.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---
    env: str = Field(default="development", alias="ENV")
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")

    # --- Database (sync SQLAlchemy + psycopg v3) ---
    database_url: str = Field(alias="DATABASE_URL")

    # --- JWT / auth ---
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_ttl_minutes: int = Field(default=15, alias="ACCESS_TOKEN_TTL_MINUTES")
    refresh_token_ttl_days: int = Field(default=14, alias="REFRESH_TOKEN_TTL_DAYS")
    password_reset_ttl_minutes: int = Field(
        default=60, alias="PASSWORD_RESET_TTL_MINUTES"
    )

    # --- Email ---
    email_mode: str = Field(default="test", alias="EMAIL_MODE")  # test | smtp

    # --- MinIO (interface only wired now) ---
    minio_endpoint: str = Field(default="minio:9000", alias="MINIO_ENDPOINT")
    minio_root_user: str = Field(default="minioadmin", alias="MINIO_ROOT_USER")
    minio_root_password: str = Field(default="", alias="MINIO_ROOT_PASSWORD")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")

    # --- First super-admin seed ---
    superadmin_email: str = Field(default="", alias="SUPERADMIN_EMAIL")
    superadmin_password: str = Field(default="", alias="SUPERADMIN_PASSWORD")
    superadmin_full_name: str = Field(
        default="Platform Super Admin", alias="SUPERADMIN_FULL_NAME"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
