"""Production hardening: probes, request IDs, security headers, rate limiting, config validation,
structured logs, error handling and the LLM gateway's fallback / circuit-breaker behaviour."""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from verity.api import main
from verity.api.middleware import TokenBucket
from verity.config import Settings, settings
from verity.llm import gateway
from verity.observability.logs import JsonFormatter, request_id_var
from verity.security import redact


@pytest.fixture()
def client(mdb):
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


def test_probes_and_version(client):
    assert client.get("/healthz").json() == {"ok": True}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["ready"] and r.json()["mongo_ms"] >= 0
    v = client.get("/version").json()
    assert v["version"] == main.app.version and "api_key" not in json.dumps(v)


def test_request_id_and_security_headers(client):
    r = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    # malformed inbound IDs are replaced, never reflected
    bad = client.get("/health", headers={"X-Request-ID": "<script>"})
    assert bad.headers["X-Request-ID"] != "<script>" and len(bad.headers["X-Request-ID"]) == 16
    assert "Content-Security-Policy" not in client.get("/docs").headers


def test_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    body = {"engagement_id": "acme-payments", "query": "ERR-4471", "mode": "text"}
    codes = [client.post("/search", json=body).status_code for _ in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429
    r = client.post("/search", json=body)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and r.headers["X-Request-ID"]
    assert client.get("/health").status_code == 200  # reads are not limited


def test_token_bucket_refills():
    b = TokenBucket(60, burst=2)
    assert b.take("k", now=0) == 0 and b.take("k", now=0) == 0
    assert b.take("k", now=0) == pytest.approx(1.0)
    assert b.take("k", now=1.0) == 0  # 1 token/s refill
    assert b.take("other", now=0) == 0  # keys are independent


def test_unhandled_errors_do_not_leak_internals(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret connection string mongodb+srv://u:p@x")

    monkeypatch.setattr(main.service, "ask", boom)
    r = client.post("/ask", json={"engagement_id": "acme-payments", "question": "anything at all?"})
    assert r.status_code == 500 and r.json()["detail"] == "internal error" and r.json()["request_id"]
    assert "mongodb" not in r.text


def test_llm_errors_map_to_502(client, monkeypatch):
    def down(*a, **k):
        raise gateway.LLMError("all models unavailable")

    monkeypatch.setattr(main.service, "ask", down)
    r = client.post("/ask", json={"engagement_id": "acme-payments", "question": "anything at all?"})
    assert r.status_code == 502 and "retry" in r.json()["detail"]


def test_production_config_validation(monkeypatch):
    s = Settings()
    for k, v in {"env": "production", "api_key": "", "mongodb_uri": "mongomock://", "llm_provider": "fake",
                 "embedding_provider": "hash"}.items():
        monkeypatch.setattr(s, k, v)
    problems = " | ".join(s.problems())
    assert "VERITY_API_KEY" in problems and "mongomock" in problems and "offline providers" in problems
    monkeypatch.setattr(s, "gemini_backend", "vertex")
    monkeypatch.setattr(s, "gcp_project", "")
    assert any("GOOGLE_CLOUD_PROJECT" in p for p in s.problems())
    assert "api_key" not in s.summary() and s.summary()["auth"] is False


def test_production_startup_refuses_bad_config(monkeypatch, mdb):
    monkeypatch.setattr(settings, "env", "production")
    with pytest.raises(RuntimeError, match="invalid production configuration"), TestClient(main.app):
        pass


def test_gemini_backend_override(monkeypatch):
    from verity.llm import google_client

    monkeypatch.setattr(settings, "gcp_project", "my-project")
    monkeypatch.setattr(settings, "gemini_backend", "auto")
    assert google_client.backend() == "vertex"
    monkeypatch.setattr(settings, "gemini_backend", "api")
    assert google_client.backend() == "gemini-api"


def test_json_log_format_is_cloud_logging_compatible():
    rec = logging.LogRecord("verity.test", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
    rec.route = "/ask"
    token = request_id_var.set("rid-1")
    try:
        doc = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert doc["severity"] == "WARNING" and doc["message"] == "hello world"
    assert doc["request_id"] == "rid-1" and doc["route"] == "/ask"


class _Err(Exception):
    def __init__(self, code, delay=None):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.details = {"error": {"details": [{"retryDelay": f"{delay}s"}]}} if delay else None


@pytest.fixture()
def live_gateway(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "model_fallbacks", ["backup-model"])
    monkeypatch.setattr(gateway.time, "sleep", lambda s: None)
    gateway._cooldown.clear()
    yield
    gateway._cooldown.clear()


def test_gateway_falls_back_on_429_and_opens_circuit(live_gateway, monkeypatch):
    calls = []

    def fake_call(model, system, prompt, schema, tier="fast"):
        calls.append(model)
        if model == settings.model_fast:
            raise _Err(429, delay=20)
        return '{"ok": true}', 10, 5

    monkeypatch.setattr(gateway, "_call_gemini", fake_call)
    assert gateway.generate("t", "sys", "prompt", schema={"type": "object"}) == {"ok": True}
    assert calls == [settings.model_fast, "backup-model"]
    # the primary is cooling down now, so the next call goes straight to the fallback
    calls.clear()
    gateway.generate("t", "sys", "prompt")
    assert calls == ["backup-model"]


def test_gateway_raises_after_exhausting_chain(live_gateway, monkeypatch):
    def overloaded(*a, **k):
        raise _Err(503)

    monkeypatch.setattr(gateway, "_call_gemini", overloaded)
    with pytest.raises(gateway.LLMError, match="all models unavailable"):
        gateway.generate("t", "sys", "prompt")


def test_gateway_does_not_retry_client_errors(live_gateway, monkeypatch):
    calls = []

    def bad_request(model, *a, **k):
        calls.append(model)
        raise _Err(400)

    monkeypatch.setattr(gateway, "_call_gemini", bad_request)
    with pytest.raises(gateway.LLMError, match="call failed"):
        gateway.generate("t", "sys", "prompt")
    assert calls == [settings.model_fast]


def test_gateway_rejects_invalid_json(live_gateway, monkeypatch):
    monkeypatch.setattr(gateway, "_call_gemini", lambda *a, **k: ("not json", 1, 1))
    with pytest.raises(gateway.LLMError, match="invalid JSON"):
        gateway.generate("t", "sys", "prompt", schema={"type": "object"})


@pytest.mark.parametrize("text,secret", [
    ("key AKIAABCDEFGHIJKLMNOP here", "AKIAABCDEFGHIJKLMNOP"),  # gitleaks:allow - fake fixture
    ("db mongodb+srv://admin:hunter2@cluster0.example.net/x", "hunter2"),
    ("contact jane.doe@example.com today", "jane.doe@example.com"),
    ("password: Sup3rS3cret!", "Sup3rS3cret!"),
])
def test_redaction_removes_secrets_before_embedding(text, secret):
    clean, findings = redact(text)
    assert secret not in clean and findings


def test_off_record_statements_are_never_stored(client):
    r = client.post("/feedback", json={"engagement_id": "acme-payments", "type": "client_statement",
                                       "content": "Off the record, the DR test actually failed"})
    assert r.status_code == 400 and "off the record" in r.json()["detail"]
    assert not client.get("/memories", params={"engagement_id": "acme-payments"}).json()
