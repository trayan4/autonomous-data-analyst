"""
OpenTelemetry spans over the graph and model calls.

OpenTelemetry IS used whenever the system runs (opentelemetry-sdk is a core
dependency). The "soft dependency" wrapper below is a robustness measure: if the
opentelemetry packages are somehow absent, every function here degrades to a no-op so
the agent keeps working, just without traces.
Nothing is configured at import time -
an entry point calls configure() (or configure_from_env()) once; library code only
opens spans.

Exporters:
  - "console"  human-readable spans to stdout (opt-in; verbose)
  - "memory"   in-memory, returned for tests
  - "azure"    batched export to Azure Monitor / Application Insights (ADA_TRACE=azure)
  - "none"     spans created but dropped (default)

Model-call spans follow the OpenTelemetry GenAI semantic conventions (gen_ai.*), plus
an added gen_ai.usage.cost_usd and an ada.model_tier attribute.
"""
from __future__ import annotations

import atexit
import os
import sys
from contextlib import contextmanager

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        SimpleSpanProcessor, ConsoleSpanExporter, BatchSpanProcessor)
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    _OTEL = True
except Exception:  # pragma: no cover - OTel not installed
    _OTEL = False

SERVICE_NAME = "autonomous-data-analyst"

_configured = False
_provider = None


def _azure_processor():
    """Build a BatchSpanProcessor that ships spans to Azure Monitor / Application
    Insights. Returns None (with a stderr note) if the connection string is missing or
    the exporter package is not installed - so 'azure' degrades instead of crashing."""
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        print("[tracing] ADA_TRACE=azure but APPLICATIONINSIGHTS_CONNECTION_STRING "
              "is not set - traces will not be exported.", file=sys.stderr)
        return None
    try:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    except Exception:
        print("[tracing] azure-monitor-opentelemetry-exporter not installed - run "
              "`pip install azure-monitor-opentelemetry-exporter`.", file=sys.stderr)
        return None
    # Batch (not Simple) for a network exporter; flushed on shutdown()/atexit.
    return BatchSpanProcessor(AzureMonitorTraceExporter(connection_string=conn))


def configure(exporter: str = "none", force: bool = False):
    """Install a tracer provider with the chosen exporter. Idempotent (first call wins
    unless force=True and OTel allows it). Returns the InMemorySpanExporter when
    exporter=='memory', else None. No-op if OpenTelemetry is not installed."""
    global _configured, _provider
    if not _OTEL:
        return None
    if _configured and not force:
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    mem = None
    if exporter == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif exporter == "memory":
        mem = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(mem))
    elif exporter == "azure":
        az = _azure_processor()
        if az is not None:
            provider.add_span_processor(az)
    # "none" -> provider with no processor; spans are created and dropped.
    trace.set_tracer_provider(provider)
    _provider = provider
    _configured = True
    atexit.register(shutdown)   # flush batched spans even if a caller forgets
    return mem


def configure_from_env():
    """Configure from ADA_TRACE (console|memory|azure|none). Default none. Lets the eval
    opt in with `ADA_TRACE=azure ada-eval`. Loads .env first (from the
    root, deterministically) so ADA_TRACE and APPLICATIONINSIGHTS_CONNECTION_STRING can
    live there rather than the shell, regardless of the working directory."""
    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except Exception:
        pass
    return configure(os.getenv("ADA_TRACE", "none"))


def flush():
    """Force-export any batched spans now (e.g. before a short script exits)."""
    if _OTEL and _provider is not None:
        _provider.force_flush()


def shutdown():
    """Flush and shut down the provider. Safe to call more than once."""
    global _provider
    if _OTEL and _provider is not None:
        try:
            _provider.shutdown()
        except Exception:  # pragma: no cover - already shut down
            pass


@contextmanager
def span(name: str, **attrs):
    """Open a span (current-context child). Yields the span, or None if OTel absent.
    None-valued attributes are skipped."""
    if not _OTEL:
        yield None
        return
    tracer = trace.get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)
        yield s


def set_attrs(s, **attrs):
    """Set attributes on a span if it exists (safe when OTel absent / span is None)."""
    if s is not None:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)


if __name__ == "__main__":
    configure("console")
    with span("graph.run", **{"ada.question": "demo"}) as root:
        with span("node.router", **{"ada.node": "router"}):
            pass
        with span("chat gpt-4.1", **{"gen_ai.system": "azure.ai.openai",
                                     "gen_ai.request.model": "gpt-4.1",
                                     "gen_ai.usage.input_tokens": 1200,
                                     "gen_ai.usage.output_tokens": 300,
                                     "gen_ai.usage.cost_usd": 0.0048,
                                     "ada.model_tier": "strong"}):
            pass
        set_attrs(root, **{"ada.route": "analysis"})
    print("emitted demo trace")
