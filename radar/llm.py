"""Tanak omotač oko LLM-a (Anthropic ili Gemini): strukturisan JSON izlaz validiran pydantic šemom.

- jedan ponovni pokušaj sa porukom o grešci ako izlaz nije validan JSON / ne prolazi šemu
- sistemski prompt ide sa cache_control (kešira se kad je dovoljno dugačak)
- greška API-ja (mreža, limit, potrošen budžet) -> LLMUnavailable; pozivalac degradira rang
"""

import json
import re
from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from radar.gemini import GeminiClient, GeminiError

T = TypeVar("T", bound=BaseModel)


# greške provajdera koje agenti hvataju (nedostupno, limit, blokiran odgovor)
API_ERRORS = (anthropic.APIError, GeminiError)


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    """API nedostupan, limit prekoračen ili budžet potrošen."""


class LLMInvalidOutput(LLMError):
    """Model nije vratio ispravan JSON ni posle ponovnog pokušaja."""


class LLM(Protocol):
    def complete_json(
        self, *, system: str, user: str, schema: type[T], model: str | None = None
    ) -> T: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> str:
    text = _FENCE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("odgovor ne sadrži JSON objekat")
    return text[start : end + 1]


class AnthropicLLM:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        client: anthropic.Anthropic | None = None,
        max_tokens: int = 600,
    ):
        self._client = client or anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=30.0)
        self._model = default_model
        self._max_tokens = max_tokens

    def complete_json(
        self, *, system: str, user: str, schema: type[T], model: str | None = None
    ) -> T:
        messages: list[dict] = [{"role": "user", "content": user}]
        for attempt in (1, 2):
            try:
                resp = self._client.messages.create(
                    model=model or self._model,
                    max_tokens=self._max_tokens,
                    system=[
                        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                    ],
                    messages=messages,
                )
            except anthropic.APIError as exc:
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                return schema.model_validate(json.loads(extract_json(text)))
            except ValueError as exc:  # JSONDecodeError i pydantic ValidationError
                if attempt == 2:
                    raise LLMInvalidOutput(f"neispravan izlaz: {exc}") from exc
                messages += [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"Izlaz nije validan ({exc}). Vrati samo ispravan JSON objekat.",
                    },
                ]
        raise AssertionError("unreachable")


class GeminiLLM:
    """Isti interfejs kao AnthropicLLM, preko Gemini REST-a (JSON mod + jedan ponovni pokušaj)."""

    def __init__(
        self,
        api_key: str,
        default_model: str,
        client: GeminiClient | None = None,
        max_tokens: int = 600,
    ):
        self._client = client or GeminiClient(api_key)
        self._model = default_model
        self._max_tokens = max_tokens

    def complete_json(
        self, *, system: str, user: str, schema: type[T], model: str | None = None
    ) -> T:
        messages: list[dict] = [{"role": "user", "content": user}]
        for attempt in (1, 2):
            try:
                resp = self._client.messages.create(
                    model=model or self._model,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=messages,
                    json_mode=True,
                )
            except GeminiError as exc:
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                return schema.model_validate(json.loads(extract_json(text)))
            except ValueError as exc:
                if attempt == 2:
                    raise LLMInvalidOutput(f"neispravan izlaz: {exc}") from exc
                messages += [
                    {"role": "assistant", "content": text or "{}"},
                    {
                        "role": "user",
                        "content": f"Izlaz nije validan ({exc}). Vrati samo ispravan JSON objekat.",
                    },
                ]
        raise AssertionError("unreachable")
