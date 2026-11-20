"""Embedding providers: Vertex AI (gemini-embedding-001) or offline hash embeddings."""
from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

from .config import settings


class Embedder:
    name: str
    dim: int

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        raise NotImplementedError


class GeminiEmbedder(Embedder):
    """gemini-embedding-001 via Vertex AI or the Gemini Developer API (see llm/google_client.py)."""
    BATCH = 50

    def __init__(self):
        from .llm.google_client import backend, gemini_client

        self.client = gemini_client()
        self.backend = backend()
        self.name = settings.embedding_model
        self.dim = settings.embedding_dim

    def embed(self, texts, kind="document"):
        from google.genai import types

        task = "RETRIEVAL_DOCUMENT" if kind == "document" else "RETRIEVAL_QUERY"
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = texts[i:i + self.BATCH]
            # Vertex AI accepts one input per request for gemini-embedding models; the Developer API batches.
            if self.backend == "vertex" and self.name.startswith("gemini-embedding"):
                for t in batch:
                    r = self._call(t, types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim))
                    out.append(_norm(r.embeddings[0].values))
            else:
                r = self._call(batch, types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim))
                out.extend(_norm(e.values) for e in r.embeddings)
        return out

    def _call(self, contents, cfg, attempts: int = 4):
        import time

        from .llm.gateway import _retry_delay, _status

        for i in range(attempts):
            try:
                return self.client.models.embed_content(model=self.name, contents=contents, config=cfg)
            except Exception as e:
                if _status(e) != 429 or i == attempts - 1:
                    raise
                time.sleep(min(65.0, _retry_delay(e) or 10.0 * (i + 1)) + 1)


_TOK = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class HashEmbedder(Embedder):
    """Hashed unigrams + bigrams. Lexical only, but deterministic and dependency-free (tests / offline)."""

    def __init__(self, dim: int):
        self.name, self.dim = f"hash-{dim}", dim

    def embed(self, texts, kind="document"):
        out = []
        for t in texts:
            toks = _TOK.findall(t.lower())
            v = [0.0] * self.dim
            for g in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]:
                h = int(hashlib.md5(g.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
            out.append(_norm(v))
        return out


def _norm(v) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))  # inputs are L2-normalised


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    if settings.embedding_provider == "hash":
        return HashEmbedder(settings.embedding_dim)
    if settings.embedding_provider in ("gemini", "vertex"):
        return GeminiEmbedder()
    raise ValueError(f"unknown EMBEDDING_PROVIDER {settings.embedding_provider!r} (gemini | vertex | hash)")
