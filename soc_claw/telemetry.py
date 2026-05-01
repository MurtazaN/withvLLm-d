"""OpenTelemetry tracing bootstrap for SOC-Claw.

Calling ``setup_tracing()`` configures a ``TracerProvider`` with an OTLP
exporter. When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset, the function
short-circuits and the OTEL API returns no-op tracers — dev runs are
unaffected.

MUST be called at module import time, before ``FastAPI(...)`` is
constructed, so ``FastAPIInstrumentor.instrument()`` (which patches the
class) takes effect on every app instance created afterward.

Also auto-instruments httpx (used by ``openai.AsyncOpenAI``) so every
``POST /v1/chat/completions`` becomes a child span automatically.
"""

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(service_name: str = "soc-claw") -> None:
    """Initialize OTEL tracing. Idempotent; no-op when endpoint is unset."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    )
    trace.set_tracer_provider(provider)

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor().instrument()
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass


def get_tracer() -> trace.Tracer:
    """Return the soc-claw tracer (no-op when ``setup_tracing`` wasn't called)."""
    return trace.get_tracer("soc-claw")
