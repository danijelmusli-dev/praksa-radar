from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    rerank_model: str = "claude-haiku-5-5"  # parsiranje profila i rerank
    embed_model: str = "voyage-3.5-lite"  # 1024 dimenzije


@lru_cache
def get_settings() -> Settings:
    return Settings()
