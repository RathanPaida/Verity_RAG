"""Retrieval & answer metrics (pure functions). Relevance is judged at source level (source_key)."""
from __future__ import annotations

import math


def source_key(source_or_chunk_id: str) -> str:
    """'acme-payments:arch-overview@v2#7' (or legacy 'arch-overview@v2#7') -> 'arch-overview'."""
    return source_or_chunk_id.split("#")[0].split(":", 1)[-1].split("@")[0]


def dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def recall_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return float("nan")
    return len(set(dedupe(retrieved)[:k]) & set(expected)) / len(set(expected))


def hit_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return float("nan")
    return 1.0 if set(dedupe(retrieved)[:k]) & set(expected) else 0.0


def mrr(retrieved: list[str], expected: list[str]) -> float:
    if not expected:
        return float("nan")
    for i, s in enumerate(dedupe(retrieved), 1):
        if s in expected:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return float("nan")
    ranked = dedupe(retrieved)[:k]
    dcg = sum(1 / math.log2(i + 1) for i, s in enumerate(ranked, 1) if s in expected)
    idcg = sum(1 / math.log2(i + 1) for i in range(1, min(len(expected), k) + 1))
    return dcg / idcg if idcg else 0.0


def citation_precision(cited_sources: list[str], expected: list[str]) -> float:
    if not cited_sources:
        return float("nan")
    return sum(1 for s in cited_sources if s in expected) / len(cited_sources)


def mean(xs: list[float]) -> float:
    xs = [x for x in xs if not math.isnan(x)]
    return sum(xs) / len(xs) if xs else float("nan")


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    idx = min(len(xs) - 1, max(0, math.ceil(p / 100 * len(xs)) - 1))
    return xs[idx]


def fact_recall(answer: str, must_include: list[str]) -> float:
    """Share of required facts present in the answer; 'a|b' means any alternative counts."""
    if not must_include:
        return float("nan")
    a = (answer or "").lower()
    return sum(any(alt.strip().lower() in a for alt in fact.split("|")) for fact in must_include) / len(must_include)


def violates(answer: str, must_not_include: list[str]) -> bool:
    a = (answer or "").lower()
    return any(s.lower() in a for s in must_not_include)


def bootstrap_ci(values: list[float], n: int = 2000, alpha: float = 0.05, seed: int = 7) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean (NaNs ignored). Makes small-sample uncertainty explicit."""
    import random

    xs = [x for x in values if not math.isnan(x)]
    if len(xs) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return (means[int(alpha / 2 * n)], means[int((1 - alpha / 2) * n) - 1])
