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
    research_model: str = "claude-sonnet-5-5"  # agent 3, faza istraživanja (alati)
    draft_model: str = "claude-sonnet-5-5"  # analiza i nacrti
    brave_api_key: str = ""  # opciono: web_search za istraživanje firme
    owner_terms: str = ""  # zarezom odvojeno (ime, mejl...): upiti sa njima se odbijaju
    github_token: str = ""  # u Actions: ${{ github.token }}; čita javne repoe
    github_repository_owner: str = ""  # u Actions postavlja GitHub
    github_user: str = ""  # lokalno: čiji se javni repoi sinhronizuju
    agents_enabled: str = "true"  # repo varijabla AGENTS_ENABLED; "false" gasi agente
    auto_analysis_max: int = 3  # automatska analiza za relevantnost >= 8, najviše po rundi
    task_payload: str = ""  # JSON iz repository_dispatch (python -m radar.tasks)


@lru_cache
def get_settings() -> Settings:
    return Settings()
