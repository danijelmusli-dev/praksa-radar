from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Podrazumevani modeli po provajderu (rerank = jeftin/brz, ostalo = jači). Gemini koristi "latest"
# aliase jer se tačna imena modela na besplatnom nivou menjaju; svaki se može pregaziti env-om.
MODEL_DEFAULTS = {
    "anthropic": {
        "rerank_model": "claude-haiku-5-5",
        "research_model": "claude-sonnet-5-5",
        "draft_model": "claude-sonnet-5-5",
    },
    "gemini": {
        "rerank_model": "gemini-flash-lite-latest",
        "research_model": "gemini-flash-latest",
        "draft_model": "gemini-flash-latest",
    },
}
EMBED_DEFAULTS = {"voyage": "voyage-3.5-lite", "gemini": "gemini-embedding-001"}  # 1024 dim


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    llm_provider: str = ""  # "gemini" | "anthropic"; prazno = gemini ako ima GEMINI_API_KEY
    gemini_api_key: str = ""  # besplatan nivo: aistudio.google.com
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    rerank_model: str = ""  # parsiranje profila i rerank (prazno = podrazumevano za provajdera)
    embed_model: str = ""  # uvek 1024 dimenzije
    research_model: str = ""  # agent 3, faza istraživanja (alati)
    draft_model: str = ""  # analiza i nacrti
    brave_api_key: str = ""  # opciono: web_search za istraživanje firme
    owner_terms: str = ""  # zarezom odvojeno (ime, mejl...): upiti sa njima se odbijaju
    github_token: str = ""  # u Actions: ${{ github.token }}; čita javne repoe
    github_repository_owner: str = ""  # u Actions postavlja GitHub
    github_user: str = ""  # lokalno: čiji se javni repoi sinhronizuju
    agents_enabled: str = "true"  # repo varijabla AGENTS_ENABLED; "false" gasi agente
    auto_analysis_max: int = 3  # automatska analiza za relevantnost >= 8, najviše po rundi
    task_payload: str = ""  # JSON iz repository_dispatch (python -m radar.tasks)
    # Faza 5
    instagram_access_token: str = ""  # Graph API (Business nalog + Meta app review)
    instagram_user_id: str = ""  # ID mog Instagram Business naloga
    healthcheck_url: str = ""  # healthchecks.io ping posle svake uspešne runde
    worker_url: str = ""  # https://praksa-radar.<nalog>.workers.dev (međusobna provera)
    github_repository: str = ""  # "korisnik/repo"; u Actions ga postavlja GitHub
    gh_app_id: str = ""  # GitHub App (agent 1 i 2): samo contents + PR u ovom repou
    gh_app_installation_id: str = ""
    gh_app_private_key: str = ""  # PEM
    stale_hours: int = 26  # runda starija od ovoga = sistem je stao

    @model_validator(mode="after")
    def _fill_model_defaults(self):
        for field, value in MODEL_DEFAULTS[self.provider].items():
            if not getattr(self, field):
                setattr(self, field, value)
        if not self.embed_model:
            self.embed_model = EMBED_DEFAULTS[self.embed_provider]
        return self

    @property
    def provider(self) -> str:
        """LLM provajder: eksplicitni LLM_PROVIDER, inače gemini ako postoji njegov ključ."""
        if self.llm_provider in MODEL_DEFAULTS:
            return self.llm_provider
        return "gemini" if self.gemini_api_key else "anthropic"

    @property
    def llm_key(self) -> str:
        return self.gemini_api_key if self.provider == "gemini" else self.anthropic_api_key

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_key)

    @property
    def embed_provider(self) -> str:
        return "gemini" if self.gemini_api_key else "voyage"

    @property
    def embed_key(self) -> str:
        return self.gemini_api_key if self.embed_provider == "gemini" else self.voyage_api_key

    @property
    def has_embedder(self) -> bool:
        return bool(self.embed_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
