"""LLM-as-judge faithfulness: is every factual statement in the brief supported by the passages it cites?

Uses a separate judge prompt (and the strong model tier) so the generator doesn't grade its own homework.
Offline (LLM_PROVIDER=fake) it falls back to a lexical support check per cited sentence.
"""
from __future__ import annotations

import re

from verity.llm.gateway import generate
from verity.retrieval.hybrid import stems

JUDGE = """You are an evaluation judge for a retrieval-augmented system. You receive an answer containing citation
labels like [S3] and the exact passages for those labels. Split the answer into atomic factual statements
(ignore headings, formatting and generic recommendations). For each statement decide if it is SUPPORTED by
the passages it cites (or, if it cites nothing, by any passage). Be strict about numbers, dates and names.
Return the counts and list the unsupported statements verbatim."""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["total_statements", "supported_statements", "unsupported"],
    "properties": {
        "total_statements": {"type": "integer"},
        "supported_statements": {"type": "integer"},
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
}

_CITE = re.compile(r"\[(S\d+)\]")


def _lexical(answer: str, passages: dict[str, str]) -> dict:
    total, ok, bad = 0, 0, []
    for sent in re.split(r"(?<=[.!?])\s+|\n", answer):
        labs = _CITE.findall(sent)
        if not labs:
            continue
        total += 1
        claim = stems(_CITE.sub("", sent))
        support = stems(" ".join(passages.get(lab, "") for lab in labs))
        if claim and len(claim & support) / len(claim) >= 0.5:
            ok += 1
        else:
            bad.append(sent.strip())
    return {"total_statements": total, "supported_statements": ok, "unsupported": bad}


def faithfulness(answer: str, citations: list[dict]) -> dict:
    from verity.agents.nodes import normalize_citations

    answer = normalize_citations(answer or "")
    passages = {c["label"]: c["passage"] for c in citations}
    if not answer or not passages:
        return {"score": float("nan"), "total_statements": 0, "supported_statements": 0, "unsupported": []}
    block = "\n".join(f'<passage label="{k}">\n{v[:1500]}\n</passage>' for k, v in passages.items())
    out = generate("judge", JUDGE, f"<answer>\n{answer}\n</answer>\n\n{block}", schema=JUDGE_SCHEMA,
                   tier="strong", fallback=lambda: _lexical(answer, passages))
    total = max(out["total_statements"], 1)
    return {**out, "score": round(min(out["supported_statements"], total) / total, 3)}
