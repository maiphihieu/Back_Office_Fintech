"""Application configuration loaded from environment variables."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for the Fintech Agent application."""

    # --- App ---
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # --- LLM ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    mock_llm: bool = True
    llm_timeout: int = 30

    # --- Supabase ---
    supabase_enabled: bool = False
    supabase_url: str = ""
    supabase_key: str = ""  # repr=False handled by model_config
    supabase_schema: str = "public"

    # --- Logging ---
    log_level: str = "INFO"

    # --- Direct PostgreSQL (migration scripts only, not used at runtime) ---
    database_url: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @model_validator(mode="after")
    def _validate_supabase(self) -> "Settings":
        """Validate Supabase config: if enabled, URL and key must be set."""
        # Auto-clean URL: supabase-py expects base URL without /rest/v1/
        if self.supabase_url:
            url = self.supabase_url.rstrip("/")
            if url.endswith("/rest/v1"):
                url = url[: -len("/rest/v1")]
            self.supabase_url = url

        if self.supabase_enabled:
            missing = []
            if not self.supabase_url:
                missing.append("SUPABASE_URL")
            if not self.supabase_key:
                missing.append("SUPABASE_KEY")
            if missing:
                raise ValueError(
                    f"SUPABASE_ENABLED=true but missing: {', '.join(missing)}. "
                    "Set them in .env or environment variables."
                )
        return self


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

