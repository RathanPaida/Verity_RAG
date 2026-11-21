"""Granular tracing: one Run per request, one Span per agent node / tool call.

Captures latency, input/output tokens, tokens/sec and $ cost, persists to the `runs` collection,
and (optionally) mirrors spans to OpenTelemetry -> Google Cloud Trace when OTEL_EXPORTER=gcp.
"""
from __future__ import annotations

import contextvars
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..config import PRICES

_current_run: contextvars.ContextVar[Run | None] = contextvars.ContextVar("verity_run", default=None)
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar("verity_span", default=None)

_otel_tracer = None
if os.getenv("OTEL_EXPORTER") == "gcp":  # optional dependency
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter()))
        trace.set_tracer_provider(provider)
        _otel_tracer = trace.get_tracer("verity")
    except ImportError:
        pass


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = PRICES.get(model, (0.0, 0.0))
    return (tokens_in * p_in + tokens_out * p_out) / 1_000_000


@dataclass
class Span:
    name: str
    parent: str | None = None
    start: float = field(default_factory=time.perf_counter)
    ms: float = 0.0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    llm_ms: float = 0.0
    cost_usd: float = 0.0
    tool_calls: list = field(default_factory=list)
    models: list = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    error: str | None = None

    def record_llm(self, model: str, tokens_in: int, tokens_out: int, ms: float):
        self.llm_calls += 1
        if model not in self.models:
            self.models.append(model)
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.llm_ms += ms
        self.cost_usd += cost_usd(model, tokens_in, tokens_out)

    def record_tool(self, name: str, args: dict, n_results: int, ms: float | None = None):
        ms = ms if ms is not None else (time.perf_counter() - self.start) * 1000
        self.tool_calls.append({"tool": name, "args": args, "results": n_results, "ms": round(ms, 1)})


@dataclass
class Run:
    question: str
    engagement_id: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    spans: list[Span] = field(default_factory=list)
    t0: float = field(default_factory=time.perf_counter)

    def totals(self) -> dict:
        tin = sum(s.tokens_in for s in self.spans)
        tout = sum(s.tokens_out for s in self.spans)
        llm_ms = sum(s.llm_ms for s in self.spans)
        return {
            "latency_ms": round((time.perf_counter() - self.t0) * 1000, 1),
            "llm_calls": sum(s.llm_calls for s in self.spans),
            "tool_calls": sum(len(s.tool_calls) for s in self.spans),
            "tokens_in": tin,
            "tokens_out": tout,
            "output_tokens_per_sec": round(tout / (llm_ms / 1000), 1) if llm_ms else 0.0,
            "cost_usd": round(sum(s.cost_usd for s in self.spans), 6),
            "models": sorted({m for s in self.spans for m in s.models}),
        }

    def to_doc(self) -> dict:
        spans = []
        for s in self.spans:
            d = asdict(s)
            d.pop("start")
            d["cost_usd"] = round(d["cost_usd"], 6)
            d["ms"] = round(d["ms"], 1)
            spans.append(d)
        return {"_id": self.run_id, "question": self.question, "engagement_id": self.engagement_id,
                "created_at": self.created_at, "spans": spans, "totals": self.totals()}


@contextmanager
def start_run(question: str, engagement_id: str):
    run = Run(question, engagement_id)
    token = _current_run.set(run)
    try:
        yield run
    finally:
        _current_run.reset(token)


@contextmanager
def span(name: str, **attrs):
    run = _current_run.get()
    parent = _current_span.get()
    s = Span(name=name, parent=parent.name if parent else None, attrs=attrs)
    token = _current_span.set(s)
    otel_cm = _otel_tracer.start_as_current_span(name) if _otel_tracer else None
    otel_span = otel_cm.__enter__() if otel_cm else None
    try:
        yield s
    except Exception as e:  # recorded, then re-raised
        s.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        s.ms = (time.perf_counter() - s.start) * 1000
        _current_span.reset(token)
        if run is not None:
            run.spans.append(s)
        if otel_cm is not None and otel_span is not None:
            otel_span.set_attribute("llm.tokens_in", s.tokens_in)
            otel_span.set_attribute("llm.tokens_out", s.tokens_out)
            otel_span.set_attribute("llm.cost_usd", s.cost_usd)
            otel_cm.__exit__(None, None, None)


def current_span() -> Span | None:
    return _current_span.get()


def percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(p / 100 * len(xs) + 0.5)) - 1))]
