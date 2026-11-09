"""Izbor provajdera (Gemini besplatno / Anthropic / Voyage) na jednom mestu."""

import anthropic

from radar.config import Settings
from radar.embed import Embedder, GeminiEmbedder, VoyageEmbedder
from radar.gemini import GeminiClient
from radar.llm import LLM, AnthropicLLM, GeminiLLM


def make_llm(s: Settings, model: str, max_tokens: int = 600) -> LLM | None:
    """LLM za JSON izlaz po šemi; None ako nema ključa (pozivalac degradira)."""
    if not s.has_llm:
        return None
    if s.provider == "gemini":
        return GeminiLLM(s.gemini_api_key, model, max_tokens=max_tokens)
    return AnthropicLLM(s.anthropic_api_key, model, max_tokens=max_tokens)


def make_agent_client(s: Settings, timeout: float = 60.0):
    """Klijent sa messages.create (tool use) za agente; None ako nema ključa."""
    if not s.has_llm:
        return None
    if s.provider == "gemini":
        return GeminiClient(s.gemini_api_key, timeout=timeout)
    return anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=timeout)


def make_embedder(s: Settings) -> Embedder | None:
    if not s.has_embedder:
        return None
    if s.embed_provider == "gemini":
        return GeminiEmbedder(s.gemini_api_key, s.embed_model)
    return VoyageEmbedder(s.voyage_api_key, s.embed_model)


def key_hint(s: Settings) -> str:
    return "GEMINI_API_KEY" if s.provider == "gemini" else "ANTHROPIC_API_KEY"
