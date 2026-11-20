"""Shared LangGraph state. Reducers make parallel/looping updates explicit."""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dict(a: dict, b: dict) -> dict:
    return {**a, **b}


class SubQuestion(TypedDict):
    id: str
    question: str
    specialist: str          # reliability | security | scalability | delivery | general
    kinds: list[str] | None  # metadata filter hint for retrieval
    use_records: bool        # whether structured records are relevant


class Claim(TypedDict):
    id: str
    text: str
    severity: str            # high | medium | low | info
    cites: list[str]         # evidence labels S1..Sn
    subquestion: str


class VerityState(TypedDict, total=False):
    run_id: str
    question: str
    engagement_id: str
    user_id: str
    filters: dict
    memories: list[dict]
    plan: list[SubQuestion]
    out_of_scope: bool
    evidence: Annotated[dict[str, dict], merge_dict]       # label -> evidence item (chunk / record aggregate)
    tool_log: Annotated[list[dict], operator.add]
    claims: list[Claim]
    conflicts: list[dict]
    gaps: list[str]
    verification: dict[str, Any]
    retries: int
    gap_queries: list[str]
    status: str                                           # answered | insufficient_evidence | out_of_scope
    answer: str
    discovery: list[dict]
    citations: list[dict]
