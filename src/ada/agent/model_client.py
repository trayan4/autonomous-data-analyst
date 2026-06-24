"""
model_client.py - thin, provider-agnostic chat client.

Targets the Azure AI Foundry v1 API (OpenAI-compatible) using the deployment
named in .env..

to make a one-call connectivity test: python -m ada.agent.model_client
"""
from __future__ import annotations

import time

from openai import OpenAI
from ada.agent.config import get_settings
from ada.observability import telemetry, tracing

_client: OpenAI | None = None
_settings = None


def _ensure():
    global _client, _settings
    if _client is None:
        _settings = get_settings()
        _client = OpenAI(base_url=_settings.base_url, api_key=_settings.api_key)
    return _client, _settings


def chat(messages: list[dict], tier: str = "cheap",
         temperature: float = 0.0, max_tokens: int = 800) -> str:
    """Send chat messages and return the text reply.

    tier="cheap" (default) uses AZURE_OPENAI_DEPLOYMENT; tier="strong" uses
    AZURE_OPENAI_DEPLOYMENT_STRONG. Tier choice is an eval decision (ADR-0002):
    keep the cheap model everywhere it passes, spend the strong model only where
    the eval shows it's needed.
    """
    client, s = _ensure()
    deployment = s.deployment_strong if tier == "strong" else s.deployment

    from contextlib import nullcontext
    span_cm = tracing.span(f"chat {deployment}",
                           **{"gen_ai.operation.name": "chat",
                              "gen_ai.system": "azure.ai.openai",
                              "gen_ai.request.model": deployment,
                              "ada.model_tier": tier}) if tracing else nullcontext()

    t0 = time.time()
    with span_cm as sp:
        resp = client.chat.completions.create(
            model=deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        dt = time.time() - t0
        u = getattr(resp, "usage", None)
        pt = getattr(u, "prompt_tokens", 0) if u else 0
        ct = getattr(u, "completion_tokens", 0) if u else 0
        if telemetry is not None:
            rec = telemetry.record_call(tier, deployment, pt, ct, dt)
            if tracing is not None:
                tracing.set_attrs(sp, **{"gen_ai.usage.input_tokens": pt,
                                         "gen_ai.usage.output_tokens": ct,
                                         "gen_ai.usage.cost_usd": rec.cost_usd})
    return resp.choices[0].message.content or ""


def chat_stream(messages: list[dict], tier: str = "cheap",
                temperature: float = 0.0, max_tokens: int = 800):
    """Stream a chat reply, yielding text deltas as they arrive.

    Same tier/telemetry/tracing contract as chat(), but the reply is produced
    incrementally for user-facing narration. Streaming responses normally omit the
    token counts, so we opt back in with stream_options.include_usage: the API then
    sends a final usage-only chunk (empty choices) that we read to keep cost
    telemetry and the gen_ai.* span attributes identical to the non-streaming path.
    If the endpoint doesn't return usage, counts degrade to 0 (cost unrecorded) but
    streaming still works.
    """
    client, s = _ensure()
    deployment = s.deployment_strong if tier == "strong" else s.deployment

    from contextlib import nullcontext
    span_cm = tracing.span(f"chat {deployment}",
                           **{"gen_ai.operation.name": "chat",
                              "gen_ai.system": "azure.ai.openai",
                              "gen_ai.request.model": deployment,
                              "ada.model_tier": tier,
                              "gen_ai.request.streaming": True}) if tracing else nullcontext()

    t0 = time.time()
    with span_cm as sp:
        stream = client.chat.completions.create(
            model=deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        pt = ct = 0
        for chunk in stream:
            u = getattr(chunk, "usage", None)        # final usage-only chunk
            if u:
                pt = getattr(u, "prompt_tokens", 0) or 0
                ct = getattr(u, "completion_tokens", 0) or 0
            for choice in (getattr(chunk, "choices", None) or []):
                delta = getattr(choice, "delta", None)
                text = getattr(delta, "content", None) if delta else None
                if text:
                    yield text
        dt = time.time() - t0
        if telemetry is not None:
            rec = telemetry.record_call(tier, deployment, pt, ct, dt)
            if tracing is not None:
                tracing.set_attrs(sp, **{"gen_ai.usage.input_tokens": pt,
                                         "gen_ai.usage.output_tokens": ct,
                                         "gen_ai.usage.cost_usd": rec.cost_usd})


if __name__ == "__main__":
    _, s = _ensure()
    print(f"cheap='{s.deployment}'  strong='{s.deployment_strong}'  @ {s.base_url}")
    reply = chat([{"role": "user", "content": "Reply with exactly the word: pong"}], max_tokens=5)
    print("cheap replied:", repr(reply))
    assert reply and "pong" in reply.lower(), f"unexpected reply: {reply!r}"
    strong = chat([{"role": "user", "content": "Reply with exactly the word: pong"}],
                  tier="strong", max_tokens=5)
    print("strong replied:", repr(strong))
    assert strong and "pong" in strong.lower(), f"unexpected reply: {strong!r}"
    streamed = "".join(chat_stream([{"role": "user", "content": "Reply with exactly the word: pong"}],
                                   max_tokens=5))
    print("stream replied:", repr(streamed))
    assert streamed and "pong" in streamed.lower(), f"unexpected stream: {streamed!r}"
    print("connectivity OK (both tiers + streaming)")
