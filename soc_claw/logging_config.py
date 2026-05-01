"""Centralized logging configuration for SOC-Claw.

Calling ``setup_logging()`` once at process startup configures the
**root** logger with a JSON formatter, so every log line — from
``soc-claw.*``, uvicorn, httpx, openai — emits as JSON. A
``TraceContextFilter`` injects the active OTEL ``trace_id`` /
``span_id`` into each record, enabling log↔trace correlation.

The JSON formatter is ``pythonjsonlogger.json.JsonFormatter``; it
serializes ``extra={}`` fields as top-level keys in the JSON line, so
log aggregators (Loki, Datadog, CloudWatch) can index them without
regex parsing.

Environment knobs:
- ``SOC_CLAW_LOG_LEVEL``  — ``DEBUG``/``INFO``/``WARNING``/``ERROR``;
                            defaults to ``INFO``.
- ``SOC_CLAW_LOG_FILE``   — when set, JSON appends to this path
                            instead of stderr.
"""

import logging
import os

from pythonjsonlogger.json import JsonFormatter


class TraceContextFilter(logging.Filter):
    """Inject OTEL trace_id / span_id into log records when a span is active."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from opentelemetry import trace
        except ImportError:
            return True
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        return True


def setup_logging() -> None:
    """Configure the **root** logger tree for structured JSON output."""
    level_name = os.environ.get("SOC_CLAW_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    log_file = os.environ.get("SOC_CLAW_LOG_FILE")
    if log_file:
        handler: logging.Handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(TraceContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn ships its own handlers on the "uvicorn" / "uvicorn.access"
    # loggers; clearing them forces propagation to the root JSON handler.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
