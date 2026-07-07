"""Application settings — the single source of runtime configuration.

Values come from environment variables (see ``infra/.env.template``). Business
logic never reads ``os.environ`` directly; it imports :data:`settings`.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 needs a key with at least as much entropy as the digest (32 bytes).
MIN_JWT_SECRET_BYTES = 32


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

    # --- Password hashing (Argon2id) ---
    # Defaults are None → passlib's strong production defaults are used. Override
    # with LOW values in the TEST environment only (see backend/scripts/run-tests.sh)
    # to keep the suite fast; NEVER lower these in production.
    argon2_time_cost: int | None = Field(default=None, alias="ARGON2_TIME_COST")
    argon2_memory_cost: int | None = Field(default=None, alias="ARGON2_MEMORY_COST")
    argon2_parallelism: int | None = Field(default=None, alias="ARGON2_PARALLELISM")

    # --- Email ---
    email_mode: str = Field(default="test", alias="EMAIL_MODE")  # test | smtp

    # --- MinIO / object storage (Vault module — docs/modules/vault.md §3) ---
    minio_endpoint: str = Field(default="minio:9000", alias="MINIO_ENDPOINT")
    minio_root_user: str = Field(default="minioadmin", alias="MINIO_ROOT_USER")
    minio_root_password: str = Field(default="", alias="MINIO_ROOT_PASSWORD")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    # Bucket that holds every society's objects (keys are society-prefixed).
    minio_bucket: str = Field(default="society-vault", alias="MINIO_BUCKET")
    # Browser-reachable host used when SIGNING presigned URLs. The in-cluster
    # ``minio_endpoint`` (e.g. ``minio:9000``) only resolves inside the Docker
    # network; a client on the host needs the published host (e.g.
    # ``localhost:9000``). Empty → fall back to ``minio_endpoint``.
    minio_public_endpoint: str = Field(default="", alias="MINIO_PUBLIC_ENDPOINT")

    # --- First super-admin seed ---
    superadmin_email: str = Field(default="", alias="SUPERADMIN_EMAIL")
    superadmin_password: str = Field(default="", alias="SUPERADMIN_PASSWORD")
    superadmin_full_name: str = Field(
        default="Platform Super Admin", alias="SUPERADMIN_FULL_NAME"
    )

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_strong_enough(cls, v: str) -> str:
        """Refuse to start with a weak HS256 secret (fail fast, not a warning)."""
        if len(v.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"JWT_SECRET must be at least {MIN_JWT_SECRET_BYTES} bytes for "
                "HS256. Generate one with "
                '`python -c "import secrets;print(secrets.token_urlsafe(48))"`.'
            )
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
