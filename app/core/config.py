from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

JWT_SECRET_MIN_LENGTH = 32
DEVELOPMENT_JWT_SECRET_KEY = "development-only-insecure-jwt-secret-key-do-not-use"


class Settings(BaseSettings):
    app_name: str = "ai-career-platform"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    environment: str = "development"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:5173"
    allowed_origins: str = "http://localhost:5173"
    enable_api_docs: bool = True
    database_url: str | None = None
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(default=15, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def validate_production_database_url(self) -> "Settings":
        if self.environment == "production" and not self.database_url:
            raise ValueError("database_url is required when environment is production")
        return self

    @model_validator(mode="after")
    def validate_jwt_secret_key(self) -> "Settings":
        secret_key = self.jwt_secret_key
        if self.environment == "production":
            if not secret_key:
                raise ValueError("jwt_secret_key is required when environment is production")
            if len(secret_key) < JWT_SECRET_MIN_LENGTH:
                raise ValueError(
                    "jwt_secret_key must be at least "
                    f"{JWT_SECRET_MIN_LENGTH} characters when environment is production"
                )
            return self
        if not secret_key:
            self.jwt_secret_key = DEVELOPMENT_JWT_SECRET_KEY
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def jwt_expire_seconds(self) -> int:
        return self.jwt_expire_minutes * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
