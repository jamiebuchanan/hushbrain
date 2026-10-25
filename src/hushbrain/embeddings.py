"""Embedders. Ollama for real use; a deterministic hash embedder for tests and offline demos."""
from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Protocol

import httpx

from hushbrain.config import Settings


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Bag-of-words feature hashing, L2-normalised.

    Cosine similarity between two texts rises with the words they share, which is enough to
    exercise ranking in tests. It has no semantic understanding; do not use it for real recall.
    """

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            d = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(d[:4], "big") % self.dim
            v[idx] += 1.0 if d[4] & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0.0:
            # pgvector's cosine distance is NaN for a zero vector, so never return one.
            v[0] = 1.0
            return v
        return [x / norm for x in v]


class OllamaEmbedder:
    # Ollama's embedding endpoint caps input at roughly 2k tokens whatever the model says.
    MAX_CHARS = 6000

    def __init__(self, url: str, model: str, dim: int, client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.dim = dim
        self._client = client or httpx.Client(timeout=60)

    def _truncate(self, text: str) -> str:
        if len(text) <= self.MAX_CHARS:
            return text
        cut = text.rfind("\n\n", 0, self.MAX_CHARS)
        if cut < 4000:
            cut = text.rfind("\n", 0, self.MAX_CHARS)
        if cut < 4000:
            cut = text.rfind(" ", 0, self.MAX_CHARS)
        return text[: cut if cut > 0 else self.MAX_CHARS]

    def embed(self, text: str) -> list[float]:
        text = self._truncate(text)
        if not text.strip():
            v = [0.0] * self.dim
            v[0] = 1.0
            return v
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self._client.post(
                    f"{self.url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                r.raise_for_status()
                vec = r.json()["embedding"]
                if len(vec) != self.dim:
                    raise ValueError(f"embedding dimension mismatch: got {len(vec)}, expected {self.dim}")
                return vec
            except (httpx.HTTPError, KeyError) as e:
                last = e
                time.sleep(0.5 + attempt)
        raise RuntimeError(f"embedding failed after retries: {type(last).__name__}")


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "hash":
        return HashEmbedder(settings.embed_dim)
    return OllamaEmbedder(settings.ollama_url, settings.embed_model, settings.embed_dim)


def vec_literal(v: list[float]) -> str:
    """pgvector accepts '[1.0,2.0,...]' as a string literal."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
