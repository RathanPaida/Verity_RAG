"""Agent nodes. Each node: traced span -> LLM via gateway (with an offline heuristic fallback) -> state update."""
from __future__ import annotations

import json
import re
from datetime import datetime
from functools import wraps
from typing import Any

from .. import db
from ..config import settings
from ..llm.gateway import LLMError, generate
from ..memory import store as memory
from ..observability.tracing import span
from ..retrieval.hybrid import Filters, Hit, corpus_idf, expand_context, hybrid_search, informative_coverage, tokenize
from ..retrieval.structured import SpecError, query_records, record_schema
from . import prompts as P
from .state import VerityState

FAKE_ANSWERABLE_COVERAGE = 0.36  # offline (LLM_PROVIDER=fake) only; real runs let the LLM judge answerability

RECORD_HINTS = re.compile(r"\b(how many|count|number of|trend|most|frequent|per service|total|average|sev\s?[12])\b",
                          re.I)


def traced(name):
    def deco(fn):
        @wraps(fn)
        def inner(state: VerityState):
            with span(name):
                return fn(state)
        return inner
    return deco


# ----------------------------------------------------------------------------- helpers

def _filters(state: VerityState, kinds: list[str] | None = None) -> Filters:
    f = state.get("filters") or {}
    return Filters(engagement_id=state["engagement_id"], kinds=kinds or f.get("kinds"), owners=f.get("owners"),
                   source_ids=f.get("source_ids"), date_from=f.get("date_from"), date_to=f.get("date_to"))


def _evidence_item(label: str, h: Hit) -> dict:
    return {"label": label, "type": "chunk", "chunk_id": h.chunk_id, "source_id": h.source_id, "title": h.title,
            "kind": h.kind, "section_path": h.section_path, "doc_date": h.doc_date, "text": h.text,
            "score": h.score, "vector_rank": h.vector_rank, "text_rank": h.text_rank}


def _evidence_block(evidence: dict, limit_chars: int = 1400) -> str:
    out = []
    for lab, e in evidence.items():
        date = e["doc_date"].strftime("%Y-%m-%d") if isinstance(e.get("doc_date"), datetime) else "n/a"
        path = " > ".join(e.get("section_path") or [])
        out.append(f'<evidence label="{lab}" source="{e["source_id"]}" kind="{e.get("kind")}" '
                   f'section="{path}" date="{date}">\n{e["text"][:limit_chars]}\n</evidence>')
    return "\n".join(out) or "(no evidence)"


def _memory_block(mems: list[dict]) -> str:
    return "\n".join(f"- [{m['type']}] {m['content']}" for m in mems) or "(none)"


def _record_fields(engagement_id: str) -> list[str]:
    doc = db.chunks().find_one({"engagement_id": engagement_id, "status": "active", "fields": {"$ne": None}},
                               {"fields": 1})
    return sorted((doc or {}).get("fields", {}).keys())


def _record_schema(engagement_id: str) -> str:
    return record_schema(engagement_id)


def _coverage(query: str, text: str) -> float:
    q = set(tokenize(query))
    return len(q & set(tokenize(text))) / len(q) if q else 0.0


def _first_sentences(text: str, n: int = 2) -> str:
    body = text.split("\n\n", 1)[-1] if "\n\n" in text else text
    return " ".join(re.split(r"(?<=[.!?])\s+", body.strip())[:n])


# ----------------------------------------------------------------------------- nodes

@traced("recall_memory")
def recall_memory(state: VerityState):
    return {"memories": memory.recall(state["engagement_id"], state["question"]),
            "retries": 0, "gap_queries": [], "evidence": {}, "tool_log": []}


@traced("supervisor")
def supervisor(state: VerityState):
    q = state["question"]

    def fallback():
        return {"out_of_scope": False, "subquestions": [
            {"question": q, "specialist": "general", "kinds": [], "use_records": bool(RECORD_HINTS.search(q))}]}

    out = generate("plan", P.SUPERVISOR.format(max_sub=settings.max_subquestions),
                   f"Engagement memories:\n{_memory_block(state.get('memories', []))}\n\nQuestion: {q}",
                   schema=P.SUPERVISOR_SCHEMA, tier="fast", fallback=fallback)
    plan = [{"id": f"Q{i}", "question": s["question"], "specialist": s["specialist"],
             "kinds": s.get("kinds") or None, "use_records": s.get("use_records", False)}
            for i, s in enumerate(out["subquestions"][:settings.max_subquestions], 1)]
    return {"plan": plan or fallback()["subquestions"], "out_of_scope": out["out_of_scope"]}


def _react(sub: Any, state: VerityState, labels_used: int, fields: list[str]) -> tuple[dict, list[dict]]:
    """ReAct loop for one sub-question. Returns (new evidence by label, tool log)."""
    evidence: dict[str, dict] = {}
    known_chunks = {e["chunk_id"] for e in (state.get("evidence") or {}).values() if e.get("chunk_id")}
    log: list[dict] = []
    observations: list[str] = []
    next_label = labels_used + 1
    schema_desc = _record_schema(state["engagement_id"]) if fields else "none"

    def add_hits(hits: list[Hit]) -> list[str]:
        nonlocal next_label
        labs = []
        for h in hits:
            if h.chunk_id in known_chunks:
                continue
            lab = f"S{next_label}"
            next_label += 1
            known_chunks.add(h.chunk_id)
            evidence[lab] = _evidence_item(lab, h)
            labs.append(lab)
        return labs

    for step in range(settings.max_react_steps):
        def fallback(step=step):
            if step == 0:
                return {"thought": "search documents", "action": "hybrid_search", "query": sub["question"],
                        "kinds": sub.get("kinds") or [], "spec_json": "", "chunk_id": ""}
            if step == 1 and sub.get("use_records") and fields:
                gb = "service" if "service" in fields else fields[0]
                spec = {"group_by": gb, "metric": "count"}
                if 'source "incidents"' in schema_desc:
                    spec = {"source": "incidents", "group_by": "service", "metric": "count"}
                return {"thought": "aggregate records", "action": "query_records", "query": "", "kinds": [],
                        "spec_json": json.dumps(spec), "chunk_id": ""}
            return {"thought": "enough", "action": "finish", "query": "", "kinds": [], "spec_json": "",
                    "chunk_id": ""}

        prompt = (f"Sub-question ({sub['id']}): {sub['question']}\nSuggested source kinds: {sub.get('kinds')}\n"
                  f"Observations so far:\n" + ("\n".join(observations) or "(none)"))
        act = generate("react", P.RESEARCHER.format(specialist=sub["specialist"], fields=schema_desc),
                       prompt, schema=P.RESEARCHER_SCHEMA, tier="fast", fallback=fallback)
        action = act["action"]
        if action == "finish":
            break
        with span(f"tool:{action}") as s:
            try:
                if action == "hybrid_search":
                    hits = hybrid_search(act["query"] or sub["question"], _filters(state, act.get("kinds") or None),
                                         k=5)
                    labs = add_hits(hits)
                    obs = "; ".join(f"{e} {evidence[e]['source_id']} {' > '.join(evidence[e]['section_path'])}: "
                                    f"{evidence[e]['text'][:160]!r}" for e in labs) or "no new results"
                    s.record_tool(action, {"query": act["query"], "kinds": act.get("kinds")}, len(hits))
                elif action == "query_records":
                    spec = json.loads(act["spec_json"] or "{}")
                    res = query_records(state["engagement_id"], spec)
                    if not res["rows"]:
                        obs = f"0 rows for spec {json.dumps(spec)} - check values against the field list: {schema_desc}"
                        s.record_tool(action, spec, 0)
                        observations.append(f"[step {step + 1}] {act['thought']} -> {action}: {obs}")
                        log.append({"subquestion": sub["id"], "step": step + 1, "thought": act["thought"],
                                    "action": action, "observation": obs[:500]})
                        continue
                    lab = f"S{next_label}"
                    next_label += 1
                    first = res["rows"][0]["chunk_ids"][0] if res["rows"] else None
                    src = first.split("#")[0] if first else "records"
                    evidence[lab] = {"label": lab, "type": "aggregate", "chunk_id": None, "source_id": src,
                                     "title": "Aggregation over structured records", "kind": "records",
                                     "section_path": [json.dumps(spec)], "doc_date": None,
                                     "text": json.dumps(res["rows"], default=str), "score": 1.0,
                                     "supporting_chunk_ids": [c for r in res["rows"] for c in r["chunk_ids"]]}
                    obs = (f"{lab} aggregate for spec {json.dumps(spec)}: {evidence[lab]['text'][:300]}"
                           if res["rows"] else
                           f"0 rows for spec {json.dumps(spec)} - check values against the field list: {schema_desc}")
                    s.record_tool(action, spec, len(res["rows"]))
                elif action == "expand_context":
                    labs = add_hits(expand_context(act["chunk_id"]))
                    obs = (f"expanded {act['chunk_id']}: added {labs}" if labs else
                           "no new passages - neighbours are already in the evidence; "
                           "search a different angle or finish")
                    s.record_tool(action, {"chunk_id": act["chunk_id"]}, len(labs))
                else:
                    obs = f"unknown action {action}"
            except (SpecError, json.JSONDecodeError) as e:
                obs = f"tool error: {e}"
        observations.append(f"[step {step + 1}] {act['thought']} -> {action}: {obs}")
        log.append({"subquestion": sub["id"], "step": step + 1, "thought": act["thought"], "action": action,
                    "observation": obs[:500]})
    return evidence, log


@traced("researcher")
def researcher(state: VerityState):
    gap = state.get("gap_queries") or []
    subs = ([{"id": f"G{i}", "question": g, "specialist": "general", "kinds": None, "use_records": False}
             for i, g in enumerate(gap, 1)] if gap else state["plan"])
    fields = _record_fields(state["engagement_id"])
    all_ev: dict[str, dict] = {}
    all_log: list[dict] = []
    used = len(state.get("evidence") or {})
    for sub in subs:
        ev, log = _react(sub, {**state, "evidence": {**(state.get("evidence") or {}), **all_ev}},
                         used + len(all_ev), fields)
        all_ev.update(ev)
        all_log.extend(log)
    return {"evidence": all_ev, "tool_log": all_log, "gap_queries": []}


@traced("analyst")
def analyst(state: VerityState):
    ev = state.get("evidence") or {}

    def fallback():
        # Offline heuristic: a passage "answers" when it holds most of the question's informative terms.
        idf = corpus_idf(state["engagement_id"])
        top = [(lab, e) for lab, e in ev.items() if e["type"] == "chunk"][:3]  # labels follow retrieval rank
        union_cov = informative_coverage(state["question"], " ".join(e["text"] for _, e in top), idf)
        claims = []
        if union_cov >= FAKE_ANSWERABLE_COVERAGE:
            claims = [{"text": _first_sentences(e["text"]), "severity": "medium", "cites": [lab]}
                      for lab, e in top if informative_coverage(state["question"], e["text"], idf) >= 0.15]
        if claims:
            claims += [{"text": f"Record aggregation: {e['text'][:200]}", "severity": "info", "cites": [lab]}
                       for lab, e in ev.items() if e["type"] == "aggregate"]
        return {"claims": claims, "conflicts": [],
                "gaps": [] if claims else [f"No passage addresses: {state['question']}"]}

    out = generate("analyze", P.ANALYST,
                   f"Question: {state['question']}\nSub-questions: {[s['question'] for s in state['plan']]}\n"
                   f"Engagement memories:\n{_memory_block(state.get('memories', []))}\n\n{_evidence_block(ev)}",
                   schema=P.ANALYST_SCHEMA, tier="strong", fallback=fallback)
    claims = []
    for i, c in enumerate(out["claims"], 1):
        cites = [x for x in c["cites"] if x in ev]  # drop hallucinated labels
        claims.append({"id": f"C{i}", "text": c["text"], "severity": c["severity"], "cites": cites,
                       "subquestion": ""})
    conflicts = [{**c, "side_a": [x for x in c["side_a"] if x in ev], "side_b": [x for x in c["side_b"] if x in ev]}
                 for c in out["conflicts"]]
    return {"claims": claims, "conflicts": conflicts, "gaps": out["gaps"]}


@traced("verifier")
def verifier(state: VerityState):
    ev = state.get("evidence") or {}
    claims = state.get("claims") or []

    def fallback():
        res = []
        for c in claims:
            cited = " ".join(ev[x]["text"] for x in c["cites"] if x in ev)
            ok = bool(c["cites"]) and _coverage(c["text"], cited) >= 0.6
            res.append({"claim_id": c["id"], "supported": ok, "reason": "lexical overlap check"})
        return {"results": res, "followup_queries": []}

    if claims:
        listing = "\n".join(f"{c['id']}: {c['text']} (cites {c['cites']})" for c in claims)
        out = generate("verify", P.VERIFIER, f"Claims:\n{listing}\n\n{_evidence_block(ev)}",
                       schema=P.VERIFIER_SCHEMA, tier="fast", fallback=fallback)
    else:
        out = {"results": [], "followup_queries": []}

    supported_ids = {r["claim_id"] for r in out["results"] if r["supported"]}
    unsupported = [c for c in claims if c["id"] not in supported_ids or not c["cites"]]
    kept = [c for c in claims if c["id"] in supported_ids and c["cites"]]
    retries = state.get("retries", 0)
    followups = out["followup_queries"] or [c["text"] for c in unsupported]

    if unsupported and retries < settings.max_verify_retries and followups:
        route = "research_more"
    elif not kept:
        route = "discovery"
    else:
        route = "write"
    return {
        "claims": kept if route != "research_more" else claims,
        "verification": {"results": out["results"], "unsupported": [c["text"] for c in unsupported],
                         "route": route, "attempt": retries + 1},
        "retries": retries + (1 if route == "research_more" else 0),
        "gap_queries": followups[:3] if route == "research_more" else [],
    }


def route_after_verify(state: VerityState) -> str:
    return state["verification"]["route"]


def route_after_supervisor(state: VerityState) -> str:
    return "discovery" if state.get("out_of_scope") else "researcher"


_CITE_GROUP = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")


def normalize_citations(text: str, valid: set[str] | dict | None = None) -> str:
    """'[S7, S8]' -> '[S7][S8]'; drops labels not in `valid` (hallucinated citations)."""
    def sub(m):
        labs = re.findall(r"S\d+", m.group(1))
        return "".join(f"[{lab}]" for lab in labs if valid is None or lab in valid)
    return _CITE_GROUP.sub(sub, text)


def _citations(text: str, ev: dict) -> tuple[str, list[dict]]:
    text = normalize_citations(text, ev)
    used = []
    for lab in re.findall(r"\[(S\d+)\]", text):
        if lab not in used:
            used.append(lab)
    cites = []
    for lab in used:
        e = ev[lab]
        cites.append({"label": lab, "chunk_id": e.get("chunk_id"), "source_id": e["source_id"], "title": e["title"],
                      "section_path": e.get("section_path"), "doc_date": e.get("doc_date"),
                      "passage": e["text"], "supporting_chunk_ids": e.get("supporting_chunk_ids")})
    return text, cites


@traced("writer")
def writer(state: VerityState):
    ev = state.get("evidence") or {}
    claims, conflicts, gaps = state.get("claims", []), state.get("conflicts", []), state.get("gaps", [])

    def fallback():
        rows = "\n".join(f"| {c['text'][:140]} | {c['severity']} | {' '.join(f'[{x}]' for x in c['cites'])} | "
                         f"Review with client |" for c in claims)
        md = (f"**Answer**\n\n{len(claims)} verified finding(s) for: {state['question']}\n\n"
              f"**Risk register**\n\n| Risk | Severity | Evidence | Recommendation |\n|---|---|---|---|\n{rows}\n")
        if conflicts:
            md += "\n**Sources disagree**\n\n" + "\n".join(
                f"- {c['description']} ({' '.join(f'[{x}]' for x in c['side_a'] + c['side_b'])})" for c in conflicts)
        if gaps:
            md += "\n\n**Open questions**\n\n" + "\n".join(f"- {g}" for g in gaps)
        return md

    findings = "\n".join(f"- ({c['severity']}) {c['text']} {' '.join(f'[{x}]' for x in c['cites'])}" for c in claims)
    confl = "\n".join(f"- {c['description']} A:{c['side_a']} B:{c['side_b']}" for c in conflicts) or "(none)"
    md = generate("write", P.WRITER,
                  f"Question: {state['question']}\nAnalyst preferences & client statements:\n"
                  f"{_memory_block(state.get('memories', []))}\n\nVerified findings:\n{findings}\n\n"
                  f"Conflicts:\n{confl}\n\nGaps:\n" + ("\n".join(gaps) or "(none)"),
                  tier="strong", fallback=fallback)
    md, cites = _citations(md, ev)
    return {"answer": md, "citations": cites, "status": "answered", "discovery": []}


@traced("discovery")
def discovery(state: VerityState):
    ev = state.get("evidence") or {}
    status = "out_of_scope" if state.get("out_of_scope") else "insufficient_evidence"

    def fallback():
        return {"summary": "The imported sources do not contain enough evidence to answer this reliably.",
                "questions": [{"question": f"Can you share documentation covering: {state['question']}?",
                               "why_it_matters": "Required to assess this risk with evidence rather than assumptions.",
                               "ask_whom": "Engineering lead"}]}

    found = "\n".join(f"- {e['source_id']}: {e['text'][:200]}" for e in list(ev.values())[:6]) or "(nothing)"
    try:
        out = generate("discovery", P.DISCOVERY,
                       f"Question: {state['question']}\nStatus: {status}\nGaps: {state.get('gaps')}\n"
                       f"Unsupported claims: {(state.get('verification') or {}).get('unsupported')}\n"
                       f"Closest evidence found:\n{found}", schema=P.DISCOVERY_SCHEMA, tier="fast", fallback=fallback)
    except LLMError:
        out = fallback()
    md = f"**I can't answer this from the provided sources.** {out['summary']}\n\n**Discovery Pack**\n\n" + "\n".join(
        f"{i}. {q['question']} - *{q['why_it_matters']}* (ask: {q['ask_whom']})"
        for i, q in enumerate(out["questions"], 1))
    return {"answer": md, "discovery": out["questions"], "status": status, "citations": []}
