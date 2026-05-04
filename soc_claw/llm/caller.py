"""Shared LLM call scaffold used by all three SOC-Claw agents.

Handles: endpoint selection → guided_json kwargs →
first call → Pydantic parse → retry with hint → parse again →
optional default factory → metadata attachment.
"""

import time
from typing import NamedTuple

from soc_claw.audit import log_inference, log_routing_decision
from soc_claw.llm.client import select_endpoint, guided_json_kwargs
from soc_claw.llm.json_extract import extract_json
import logging
logger = logging.getLogger("soc-claw.llm.caller")

class LLMResult(NamedTuple):
    result: dict
    inference_ms: int
    route: str
    raw_content: str


def _parse_llm_output(schema_class, content: str) -> dict | None:
    try:
        return schema_class.model_validate_json(content).model_dump()
    except Exception as e:
        logger.warning("direct JSON parse failed for %s: %s", schema_class.__name__, e)
    try:
        return schema_class.model_validate(extract_json(content)).model_dump()
    except Exception as e:
        logger.warning(
            "extract_json+validate failed for %s: %s | content head: %s",
            schema_class.__name__, e, content[:300],
        )
        return None



async def call_llm(
    agent_name: str,
    system_prompt: str,
    user_content: str,
    schema_class,
    retry_hint: str,
    default_factory=None,
    client=None,
) -> LLMResult:
    """Shared LLM call scaffold used by all three agents.

    Handles: endpoint selection → guided_json kwargs →
    first call → Pydantic parse → retry with hint → parse again →
    optional default factory → metadata attachment.

    Parameters
    ----------
    agent_name:
        Identifier for logging (``"triage"``, ``"verifier"``, ``"response"``).
    system_prompt:
        The system-role prompt for this agent.
    user_content:
        The user-role prompt (alert + enrichment / verdict).
    schema_class:
        Pydantic ``BaseModel`` subclass for the expected output. Used for
        ``guided_json`` (local route) and parse validation (all routes).
    retry_hint:
        Message sent when the first LLM response fails to parse, e.g.
        ``"Please output valid JSON with fields: ..."``.
    default_factory:
        Optional ``callable() -> dict``. Called when both the first call
        and the retry fail to parse. If ``None`` and parsing fails, an
        empty dict ``{}`` is returned.

    Returns
    -------
    LLMResult
        NamedTuple containing result dict, inference_ms, route, and raw_content.
    """
    from soc_claw.telemetry import get_tracer
    tracer = get_tracer()

    # ── Resolve endpoint from routing.yaml ─────────────────────
    selected_client, model_name, provider_name, reason = select_endpoint(agent_name, user_content)
    client_to_use = client or selected_client
    route = reason  # use the reason string as the route label

    with tracer.start_as_current_span(
        "llm.call",
        attributes={"agent": agent_name, "model": model_name},
    ) as span:
        span.set_attribute("route", route)
        span.set_attribute("route.reason", reason)
        log_routing_decision(agent_name, route, reason, user_content, provider_name, model_name)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        gj = guided_json_kwargs(schema_class, route)

        # ── First call ────────────────────────────────────────────
        inference_start = time.perf_counter()
        response = await client_to_use.chat.completions.create(
            model=model_name,
            messages=messages,
            **gj,
        )
        inference_ms = int((time.perf_counter() - inference_start) * 1000)
        log_inference(agent_name, route, inference_ms)

        content = response.choices[0].message.content or ""
        result = _parse_llm_output(schema_class, content)
        used_retry = False
        used_default = False

        # ── Retry once ────────────────────────────────────────────
        if result is None:
            used_retry = True
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": retry_hint})
            response = await client_to_use.chat.completions.create(
                model=model_name,
                messages=messages,
                **gj,
            )
            content = response.choices[0].message.content or ""
            result = _parse_llm_output(schema_class, content)

        # ── Default fallback ──────────────────────────────────────
        if result is None:
            used_default = True
            result = default_factory() if default_factory else {}

        span.set_attribute("parse.success", not used_default)
        span.set_attribute("used_retry", used_retry)
        span.set_attribute("used_default", used_default)

        # ── Attach telemetry metadata ─────────────────────────────
        result.setdefault("_meta", {}).update({
            "inference_ms": inference_ms,
            "route": route,
            "raw_response": content
        })

        return LLMResult(
            result=result,
            inference_ms=inference_ms,
            route=route,
            raw_content=content,
        )
