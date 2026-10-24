from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
