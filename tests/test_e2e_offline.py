"""End-to-end with fake LLM + hash embeddings + mongomock: ingestion, hybrid retrieval, agent graph, tracing."""
from eval.metrics import source_key
from verity.retrieval.hybrid import Filters, hybrid_search
from verity.retrieval.structured import query_records


def test_ingestion_and_versioning(ingested):
    mdb, res = ingested
    assert len(res) == 7 and all(r["status"] == "created" for r in res)
    assert mdb["chunks"].count_documents({"fields": {"$ne": None}}) == 12
    from verity.ingest.pipeline import ingest_path
    again = ingest_path("data/sample/repo-summary.md", "acme-payments")
    assert again[0]["status"] == "unchanged"


def test_hybrid_finds_exact_code_and_isolates_engagements(ingested):
    hits = hybrid_search("ERR-4471", Filters("acme-payments"), k=3)
    assert source_key(hits[0].source_id) == "postmortem-2024-03-pay-api"
    assert hits[0].text_rank is not None  # matched by the keyword (BM25) leg, not only semantically
    assert hybrid_search("ERR-4471", Filters("other-client"), k=3) == []


def test_metadata_filters(ingested):
    hits = hybrid_search("ledger-db failover", Filters("acme-payments", kinds=["runbook"]), k=5)
    assert hits and all(h.kind == "runbook" for h in hits)


def test_structured_aggregation(ingested):
    res = query_records("acme-payments", {"match": {"severity": {"in": ["SEV1", "SEV2"]}},
                                          "group_by": "service", "metric": "count"})
    top = res["rows"][0]
    assert top["group"] == "ledger-service" and top["value"] == 5


def test_agent_answers_with_citations_and_trace(ingested):
    from verity.service import ask
    r = ask("What caused the ERR-4471 timeouts on PAY-API?", "acme-payments")
    assert r["status"] == "answered"
    assert r["citations"] and any(source_key(c["source_id"]) == "postmortem-2024-03-pay-api" for c in r["citations"])
    names = [s["name"] for s in r["spans"]]
    for n in ("recall_memory", "supervisor", "researcher", "analyst", "verifier", "writer"):
        assert n in names
    assert r["metrics"]["tool_calls"] >= 1


def test_agent_abstains_on_unanswerable(ingested):
    from verity.service import ask
    r = ask("What was the result of the SOC 2 Type II audit?", "acme-payments")
    assert r["status"] == "insufficient_evidence"
    assert r["discovery"] and r["citations"] == []


def test_memory_is_recalled_into_run(ingested):
    from verity.memory import store
    from verity.service import ask
    store.remember("acme-payments", "vmk", "preference", "Focus the brief on reliability and DR risks first")
    r = ask("What caused the ERR-4471 timeouts on PAY-API?", "acme-payments")
    assert any("reliability" in m["content"] for m in r["memories_used"])


def test_record_schema_describes_values(ingested):
    from verity.retrieval.structured import record_schema
    d = record_schema("acme-payments")
    assert "severity (text: SEV1, SEV2, SEV3)" in d and "date (date" in d and "minutes_down (number" in d
