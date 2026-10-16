from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All secrets/config come from environment variables (never hard-coded)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    forager_api_key: str
    forager_account_id: int
    forager_base_url: str = "https://api-v2.forager.ai"

    hubspot_token: str
    hubspot_client_secret: str = ""

    public_base_url: str = ""
    api_secret: str = ""

    default_include_titles: str = "CEO,Chief,CTO,CFO,COO,VP,Head of,Director"
    default_exclude_titles: str = "Intern,Assistant"
    max_contacts: int = 5
    enable_extras: bool = True

    @staticmethod
    def split(value: str) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
