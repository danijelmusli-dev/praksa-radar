"""Voyage embeddingi (REST). voyage-3.5-lite sa 1024 dimenzije, što odgovara vector(1024)."""

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
