"""Structured audit logging helpers for SOC-Claw.

Each function emits a structured log record via Python's ``logging``
module with an ``extra`` dict.  The JSON formatter configured in
``soc_claw.logging_config`` serializes these as top-level keys, making
them queryable in log aggregators (Loki, Datadog, CloudWatch) without
regex parsing.
"""

import hashlib
import json
import logging

logger = logging.getLogger("soc-claw.audit")


def log_routing_decision(
    agent_name: str,
    route: str,
    reason: str,
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
):
    """Log a privacy routing decision."""
    prompt_hash = hashlib.sha256(prompt[:500].encode()).hexdigest()[:12]
    logger.info(
        "routing_decision",
        extra={
            "event": "routing_decision",
            "agent": agent_name,
            "route": route,
            "reason": reason,
            "prompt_hash": prompt_hash,
            "provider": provider,
            "model": model,
        },
    )


def log_tool_call(tool_name: str, tool_input: dict, tool_output: dict, latency_ms: int):
    """Log a tool call with timing."""
    logger.info(
        "tool_call",
        extra={
            "event": "tool_call",
            "tool": tool_name,
            "input_preview": json.dumps(tool_input)[:200],
            "latency_ms": latency_ms,
        },
    )


def log_inference(agent_name: str, route: str, latency_ms: int):
    """Log an inference request."""
    logger.info(
        "inference",
        extra={
            "event": "inference",
            "agent": agent_name,
            "route": route,
            "latency_ms": latency_ms,
        },
    )


def log_verification(alert_id: str, original: str, verified: str, decision: str, issues: list):
    """Log a verification decision."""
    logger.info(
        "verification",
        extra={
            "event": "verification",
            "alert_id": alert_id,
            "original_severity": original,
            "verified_severity": verified,
            "decision": decision,
            "issues": issues,
        },
    )


def log_response_plan(alert_id: str, num_steps: int, action_types: list, approval_count: int):
    """Log a response plan."""
    logger.info(
        "response_plan",
        extra={
            "event": "response_plan",
            "alert_id": alert_id,
            "num_steps": num_steps,
            "action_types": action_types,
            "approval_required": approval_count,
        },
    )


def log_analyst_action(alert_id: str, action: str, details: str):
    """Log an analyst action (approve/reject/steer)."""
    logger.info(
        "analyst_action",
        extra={
            "event": "analyst_action",
            "alert_id": alert_id,
            "action": action,
            "details": details,
        },
    )
