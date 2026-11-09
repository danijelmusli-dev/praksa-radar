"""Embeddingi (REST): Gemini (besplatno) ili Voyage. Uvek 1024 dimenzije = vector(1024)."""

import math
from typing import Literal, Protocol

import httpx

from radar.http import request_with_retry

URL = "https://api.voyageai.com/v1/embeddings"
DIM = 1024
BATCH = 64


class EmbedError(RuntimeError):
    pass


class Embedder(Protocol):
    def embed(
        self, texts: list[str], input_type: Literal["document", "query"]
    ) -> list[list[float]]: ...


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


class VoyageEmbedder:
    def __init__(
        self, api_key: str, model: str = "voyage-3.5-lite", client: httpx.Client | None = None
    ):
        self._key = api_key
        self._model = model
        self._client = client or httpx.Client(timeout=30)

    def embed(
        self, texts: list[str], input_type: Literal["document", "query"]
    ) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            chunk = texts[i : i + BATCH]
            try:
                resp = request_with_retry(
                    self._client,
                    "POST",
                    URL,
                    json={
                        "input": chunk,
                        "model": self._model,
                        "input_type": input_type,
                        "output_dimension": DIM,
                        "truncation": True,
                    },
                    headers={"Authorization": f"Bearer {self._key}"},
                )
            except httpx.HTTPError as exc:
                raise EmbedError(f"Voyage nedostupan: {exc}") from exc
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            vectors = [d["embedding"] for d in data]
            if len(vectors) != len(chunk) or any(len(v) != DIM for v in vectors):
                raise EmbedError("Voyage je vratio neočekivan broj ili dimenziju embeddinga")
            out += vectors
        return out


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_BATCH = 50  # batchEmbedContents prima najviše 100 tekstova; besplatan nivo ima mali TPM
MAX_EMBED_CHARS = 8000


def _normalize(v: list[float]) -> list[float]:
    # za dimenzije manje od pune Gemini ne normalizuje vektor
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class GeminiEmbedder:
    """gemini-embedding-001 sa outputDimensionality=1024 (Matryoshka), normalizovano."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-embedding-001",
        client: httpx.Client | None = None,
    ):
        self._key = api_key
        self._model = model.removeprefix("models/")
        self._client = client or httpx.Client(timeout=60)

    def embed(
        self, texts: list[str], input_type: Literal["document", "query"]
    ) -> list[list[float]]:
        task = "RETRIEVAL_DOCUMENT" if input_type == "document" else "RETRIEVAL_QUERY"
        out: list[list[float]] = []
        for i in range(0, len(texts), GEMINI_BATCH):
            chunk = texts[i : i + GEMINI_BATCH]
            try:
                resp = request_with_retry(
                    self._client,
                    "POST",
                    f"{GEMINI_BASE}/models/{self._model}:batchEmbedContents",
                    json={
                        "requests": [
                            {
                                "model": f"models/{self._model}",
                                "content": {"parts": [{"text": t[:MAX_EMBED_CHARS]}]},
                                "taskType": task,
                                "outputDimensionality": DIM,
                            }
                            for t in chunk
                        ]
                    },
                    headers={"x-goog-api-key": self._key},
                    max_attempts=4,
                    base_delay=4.0,
                )
                vectors = [e["values"] for e in resp.json()["embeddings"]]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise EmbedError(f"Gemini embedding nedostupan: {type(exc).__name__}") from exc
            if len(vectors) != len(chunk) or any(len(v) != DIM for v in vectors):
                raise EmbedError("Gemini je vratio neočekivan broj ili dimenziju embeddinga")
            out += [_normalize(v) for v in vectors]
        return out
