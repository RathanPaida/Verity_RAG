import pytest

from eval.metrics import mrr, ndcg_at_k, recall_at_k, source_key
from verity.ingest.chunker import chunk_records, chunk_text
from verity.retrieval.hybrid import bm25, rrf_fuse
from verity.retrieval.structured import SpecError, build_pipeline
from verity.security import redact

DOC = """# Title

Intro paragraph.

## Data Tier

ledger-db runs a single primary.

### Failover

Automatic within 60 seconds.
"""


def test_chunker_keeps_section_paths_and_offsets():
    chunks = chunk_text(DOC, max_tokens=50)
    paths = [c.section_path for c in chunks]
    assert ["Title", "Data Tier", "Failover"] in paths
    fail = next(c for c in chunks if c.section_path[-1] == "Failover")
    assert DOC[fail.char_start:fail.char_end] == "Automatic within 60 seconds."
    assert fail.text.startswith("Title › Data Tier › Failover")


def test_chunker_respects_budget():
    text = "# H\n\n" + "\n\n".join(f"para {i} " + "word " * 40 for i in range(10))
    chunks = chunk_text(text, max_tokens=120)
    assert len(chunks) > 3


def test_records_keep_typed_fields():
    c = chunk_records([{"service": "PAY-API", "minutes_down": 47}], "Incidents")[0]
    assert c.fields["minutes_down"] == 47 and "PAY-API" in c.text


def test_rrf_rewards_agreement():
    s = rrf_fuse([(["a", "b", "c"], 1.0), (["b", "d"], 1.0)], k=60)
    assert max(s, key=s.get) == "b"


def test_bm25_exact_code():
    docs = [{"_id": 1, "text": "upstream timeout ERR-4471 on PAY-API"}, {"_id": 2, "text": "database failover"}]
    assert bm25("ERR-4471", docs)[0][0]["_id"] == 1


def test_structured_spec_is_whitelisted():
    p = build_pipeline("e", {"match": {"severity": {"in": ["SEV1"]}}, "group_by": "service", "metric": "count"})
    assert p[0]["$match"]["fields.severity"] == {"$in": ["SEV1"]}
    for bad in ({"group_by": "service; drop"}, {"match": {"$where": "1"}}, {"group_by": "s", "metric": "eval:x"},
                {"match": {"a": {"$regex": "."}}}):
        with pytest.raises(SpecError):
            build_pipeline("e", bad)


def test_metrics():
    got = ["a", "x", "b"]
    assert recall_at_k(got, ["a", "b"], 2) == 0.5
    assert mrr(["x", "a"], ["a"]) == 0.5
    assert 0 < ndcg_at_k(got, ["a", "b"], 3) < 1
    assert source_key("arch-overview@v2#7") == "arch-overview"


def test_redaction():
    out, f = redact("service key sk-live-abcdefghijklmnop1234 and password: hunter22")
    assert "hunter22" not in out and "sk-live" not in out


def test_grouped_citations_normalized_and_hallucinations_dropped():
    from verity.agents.nodes import normalize_citations
    assert normalize_citations("x [S7, S8; S9] y [S2]", {"S7", "S9", "S2"}) == "x [S7][S9] y [S2]"


def test_gateway_circuit_breaker_falls_back_on_429(monkeypatch):
    from verity.config import settings
    from verity.llm import gateway

    class RateLimited(Exception):
        code = 429
        details = {"error": {"details": [{"retryDelay": "20s"}]}}

    calls = []

    def fake_call(m, *a, **k):
        calls.append(m)
        if m == "primary":
            raise RateLimited()
        return "ok", 1, 1

    monkeypatch.setattr(gateway, "_call_gemini", fake_call)
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "model_fallbacks", ["backup"])
    gateway._cooldown.clear()
    assert gateway._call_with_fallbacks("t", "primary", "", "", None, "fast")[3] == "backup"
    assert gateway._call_with_fallbacks("t", "primary", "", "", None, "fast")[3] == "backup"
    assert calls == ["primary", "backup", "backup"]  # primary skipped while cooling down
    gateway._cooldown.clear()


def test_bare_list_means_any_of():
    p = build_pipeline("e", {"match": {"severity": ["SEV1", "SEV2"]}, "group_by": "service"})
    assert p[0]["$match"]["fields.severity"] == {"$in": ["SEV1", "SEV2"]}
