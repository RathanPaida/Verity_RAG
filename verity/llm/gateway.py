"""Provider-agnostic LLM gateway.

    generate(task, system, prompt, schema=None, tier="fast"|"strong", fallback=callable)

Providers:
  gemini - Gemini on Vertex AI via the google-genai SDK (default, GCP-native)
  claude - Claude on Vertex AI via the Anthropic SDK (AnthropicVertex)
  fake   - offline & deterministic: returns `fallback()` so the whole graph runs without keys (dev/tests/CI)

Every call records tokens, latency and cost on the current tracing span.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from ..config import settings
from ..observability.tracing import current_span

log = logging.getLogger("verity.llm")


class LLMError(Exception):
    pass


def is_fake() -> bool:
    return settings.llm_provider == "fake"


def _model_for(tier: str) -> str:
    if settings.llm_provider == "claude":
        return settings.claude_model
    if settings.llm_provider == "fake":
        return "fake"
    return settings.model_strong if tier == "strong" else settings.model_fast


def _gemini():
    from .google_client import gemini_client

    return gemini_client()


@lru_cache(maxsize=1)
def _claude():
    from anthropic import AnthropicVertex

    return AnthropicVertex(project_id=settings.gcp_project, region=settings.gcp_location)


def _call_gemini(model: str, system: str, prompt: str, schema: dict | None,
                 tier: str = "fast") -> tuple[str, int, int]:
    from google.genai import types

    cfg = types.GenerateContentConfig(system_instruction=system)
    if model.startswith("gemini-3"):
        # Gemini 3 is tuned for the default temperature; control depth with thinking level instead.
        # Inner agent steps (plan / ReAct / verify) use low thinking for latency; analyst & writer use the default.
        if tier == "fast":
            cfg.thinking_config = types.ThinkingConfig(
                thinking_level=types.ThinkingLevel(settings.fast_thinking_level.upper()))
    else:
        cfg.temperature = 0.2
    if schema:
        cfg.response_mime_type = "application/json"
        cfg.response_json_schema = schema
    resp = _gemini().models.generate_content(model=model, contents=prompt, config=cfg)
    um = resp.usage_metadata
    # Thinking tokens are billed as output tokens.
    out_tokens = (um.candidates_token_count or 0) + (getattr(um, "thoughts_token_count", 0) or 0)
    return resp.text or "", (um.prompt_token_count or 0), out_tokens


def _call_claude(model: str, system: str, prompt: str, schema: dict | None) -> tuple[str, int, int]:
    import anthropic

    kwargs: dict[str, Any] = {"output_config": {"effort": "medium"}}
    if schema:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    try:
        resp = _claude().messages.create(model=model, max_tokens=16000, system=system,
                                         messages=[{"role": "user", "content": prompt}], **kwargs)
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach Claude on Vertex AI") from e
    if resp.stop_reason == "refusal":
        raise LLMError("model declined the request")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens


# HTTP statuses that mean "this model can't serve right now" -> try the next model in the chain.
_FALLBACK_STATUSES = {429, 500, 502, 503, 504}
_MAX_RATE_LIMIT_WAIT_S = 65.0
_cooldown: dict[str, float] = {}  # model -> unix time until which it is rate-limited


def _status(e: Exception) -> int | None:
    return getattr(e, "code", None) or getattr(e, "status_code", None)


def _is_network_error(e: Exception) -> bool:
    return _status(e) is None and (isinstance(e, (ConnectionError, TimeoutError))
                                   or type(e).__module__.split(".")[0] in ("httpx", "httpcore"))


def _retry_delay(e: Exception) -> float | None:
    """Server-suggested wait from a 429 (google.rpc.RetryInfo retryDelay, e.g. '34s')."""
    details = getattr(e, "details", None)
    if not isinstance(details, dict):
        return None
    for d in details.get("error", {}).get("details", []):
        if isinstance(d, dict) and "retryDelay" in d:
            try:
                return float(str(d["retryDelay"]).rstrip("s"))
            except ValueError:
                return None
    return None


def _try_chain(task, model, chain, system, prompt, schema, tier, errors):
    now = time.time()
    ready = [m for m in chain if _cooldown.get(m, 0) <= now]
    if not ready:
        return None
    for m in ready:
        for attempt in range(3):
            try:
                text, tin, tout = _call_gemini(m, system, prompt, schema, tier)
                if m != model:
                    log.warning("task %s served by fallback model %s (%s)", task, m, "; ".join(errors[-3:]))
                return text, tin, tout, m
            except Exception as e:  # provider SDK errors -> one error type for the graph
                st, net = _status(e), _is_network_error(e)
                if st == 429:
                    # circuit breaker: skip this model until the server-suggested delay passes
                    _cooldown[m] = time.time() + min(_MAX_RATE_LIMIT_WAIT_S, _retry_delay(e) or 30.0)
                    errors.append(f"{m}: 429")
                    break
                if attempt < 2 and net:
                    time.sleep(3 * (attempt + 1))
                    continue
                if st in _FALLBACK_STATUSES or net:
                    errors.append(f"{m}: {st or type(e).__name__}")
                    break
                raise LLMError(f"gemini call failed for task '{task}' on {m}: {e}") from e
    return None


def _call_with_fallbacks(task, model, system, prompt, schema, tier):
    """Primary model, then MODEL_FALLBACKS in order. Per model: honour 429 retryDelay (rate limits) and back off
    on dropped connections before moving on. Returns (text, tokens_in, tokens_out, model_that_answered)."""
    if settings.llm_provider == "claude":
        try:
            text, tin, tout = _call_claude(model, system, prompt, schema)
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"claude call failed for task '{task}': {e}") from e
        return text, tin, tout, model
    chain = [model] + [m for m in settings.model_fallbacks if m != model]
    errors: list[str] = []
    for round_ in range(3):  # if every model is cooling down / overloaded, wait and try the chain again
        if round_:
            soonest = min((_cooldown.get(m, 0) for m in chain), default=0) - time.time()
            time.sleep(min(_MAX_RATE_LIMIT_WAIT_S, max(10.0 * round_, soonest + 0.5)))
        result = _try_chain(task, model, chain, system, prompt, schema, tier, errors)
        if result:
            return result
    raise LLMError(f"all models unavailable for task '{task}': {'; '.join(errors[-6:])}")


def generate(task: str, system: str, prompt: str, schema: dict | None = None, tier: str = "fast",
             fallback: Callable[[], Any] | None = None) -> Any:
    """Return parsed JSON (if schema) or text. In fake mode returns fallback()."""
    model = _model_for(tier)
    span = current_span()
    t0 = time.perf_counter()

    if is_fake():
        if fallback is None:
            raise LLMError(f"fake provider has no fallback for task '{task}'")
        out = fallback()
        if span:
            # token counts are estimates; latency 0 so tokens/sec isn't reported for heuristic "generation"
            span.record_llm("fake", len(prompt) // 4, len(json.dumps(out, default=str)) // 4, 0.0)
        return out

    text, tin, tout, model = _call_with_fallbacks(task, model, system, prompt, schema, tier)

    if span:
        span.record_llm(model, tin, tout, (time.perf_counter() - t0) * 1000)
    if not schema:
        return text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid JSON for task '{task}': {text[:200]}") from e
