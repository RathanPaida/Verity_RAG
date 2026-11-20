"""API-level tests (FastAPI TestClient) on the offline stack."""
import pytest
from fastapi.testclient import TestClient

from verity.api import main


@pytest.fixture()
def client(ingested):
    with TestClient(main.app) as c:
        yield c


def test_ui_and_health(client):
    assert "Verity" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    h = client.get("/health").json()
    assert h["llm"] == "fake" and h["mongo"] == "mongomock"


def test_sources_chunks_and_search(client):
    srcs = client.get("/sources", params={"engagement_id": "acme-payments"}).json()
    assert {s["source_key"] for s in srcs} >= {"arch-overview", "incidents"}
    src = client.get("/sources/acme-payments:arch-overview@v1").json()
    assert "multi-region active-active" in src["raw_text"]
    hits = client.post("/search", json={"engagement_id": "acme-payments", "query": "ERR-4471", "mode": "text"}).json()
    assert hits[0]["source_id"].startswith("acme-payments:postmortem-2024-03-pay-api")
    chunk = client.get(f"/chunks/{hits[0]['chunk_id']}").json()
    assert src["raw_text"] or chunk["text"]


def test_upload_creates_new_version(client, tmp_path):
    md = "---\ntitle: Repo\nkind: repo\n---\n# Repo\n\nCoverage is now 70% after the Q4 push.\n"
    r = client.post("/ingest", data={"engagement_id": "acme-payments"},
                    files=[("files", ("repo-summary.md", md.encode(), "text/markdown"))]).json()
    assert r["results"][0]["source_id"] == "acme-payments:repo-summary@v2" and r["results"][0]["status"] == "updated"
    old = client.get("/sources/acme-payments:repo-summary@v1").json()
    assert old["status"] == "superseded"
    bad = client.post("/ingest", data={"engagement_id": "acme-payments"},
                      files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 415


def test_ask_cache_metrics_and_run(client):
    body = {"engagement_id": "acme-payments", "question": "What caused the ERR-4471 errors on PAY-API?"}
    first = client.post("/ask", json=body).json()
    assert first["status"] == "answered" and not first["cache"]["hit"]
    second = client.post("/ask", json=body).json()
    assert second["cache"]["hit"] and second["metrics"]["cost_usd"] == 0
    # memory changes the corpus fingerprint -> cache invalidated
    client.post("/feedback", json={"engagement_id": "acme-payments", "type": "preference",
                                   "content": "Keep briefs to three findings"})
    third = client.post("/ask", json=body).json()
    assert not third["cache"]["hit"]
    m = client.get("/metrics", params={"engagement_id": "acme-payments"}).json()
    assert m["runs"] == 2 and m["latency_ms"]["p95"] is not None
    assert {"supervisor", "researcher", "verifier"} <= {n["_id"] for n in m["nodes"]}
    run = client.get(f"/runs/{first['run_id']}").json()
    assert run["citations"] and run["totals"]["tool_calls"] >= 1


def test_eval_endpoint(client):
    r = client.post("/eval", json={"engagement_id": "acme-payments", "modes": ["text", "hybrid"], "k": 5,
                                   "agent": True, "judge": True}).json()
    assert r["summary"]["hybrid.recall"] >= 0.8
    assert r["agent"]["abstention_accuracy"] >= 0.75
    assert client.get("/eval/history", params={"engagement_id": "acme-payments"}).json()


def test_gcs_event_routing(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "ingest_path", lambda p, e: seen.update(path=p, eng=e) or [])
    ok = client.post("/events/gcs", headers={"ce-type": "google.cloud.storage.object.v1.finalized"},
                     json={"bucket": "verity-raw", "name": "acme-payments/new-postmortem.md"})
    assert ok.status_code == 200 and seen == {"path": "gs://verity-raw/acme-payments/new-postmortem.md",
                                              "eng": "acme-payments"}
    assert client.post("/events/gcs", json={}).status_code == 400


def test_api_key_enforced(client, monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", "s3cret")
    assert client.get("/engagements").status_code == 401
    assert client.get("/engagements", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_eval_gate_detects_regression():
    from eval.run_eval import gate
    assert gate({"hybrid.recall": 0.80}, {"hybrid.recall": 0.90})
    assert not gate({"hybrid.recall": 0.89}, {"hybrid.recall": 0.90})
