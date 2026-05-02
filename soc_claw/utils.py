import asyncio
import hashlib
import json
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Populate os.environ from a .env file if one is present. In production
# (k8s, CI), env vars are injected by the orchestrator and this is a no-op.
# The rest of this module reads from os.environ — same code, every env.
load_dotenv()

CONFIG_DIR = Path(__file__).parent / "config"
logger = logging.getLogger("soc-claw")


def extract_json(text: str) -> dict:
    """Robustly extract JSON from LLM response text.

    Handles markdown fences, bare JSON, and regex fallback.
    """
    # Strip markdown code fences
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.MULTILINE)
    stripped = re.sub(r"\n?```\s*$", "", stripped.strip(), flags=re.MULTILINE)

    # Try direct parse
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Try finding first { ... } block (greedy)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Try finding first [ ... ] block for arrays
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.debug("extract_json failed on full text:\n%s", text)
    head = text[:200]
    tail = text[-200:] if len(text) > 400 else ""
    preview = f"{head}...{tail}" if tail else head
    raise ValueError(f"Could not extract valid JSON from LLM response: {preview}")


@lru_cache(maxsize=1)
def load_privacy_routes() -> dict:
    """Load and cache privacy routing configuration."""
    config_path = CONFIG_DIR / "privacy_routes.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    # Default config if file doesn't exist yet
    return {
        "local_inference": {
            "route_when": [
                {"pattern": r"10\.\d+\.\d+\.\d+", "reason": "Internal IP address detected"},
                {"pattern": r"192\.168\.\d+\.\d+", "reason": "Internal IP address detected"},
                {"pattern": r"(DC-|SRV-|WS-|FW-|VPN-)", "reason": "Internal hostname detected"},
                {"pattern": r"(payload|command_line|raw_log)", "reason": "Alert payload content detected"},
                {"pattern": r"(employee|user_id|email)", "reason": "Employee identifier detected"},
            ]
        }
    }


def route_request(prompt: str) -> tuple[str, str]:
    """Determine whether to route inference locally or to cloud.

    Returns (route, reason) where route is 'local' or 'cloud'.
    """
    config = load_privacy_routes()
    for rule in config.get("local_inference", {}).get("route_when", []):
        pattern = rule.get("pattern", "")
        if pattern and re.search(pattern, prompt):
            return ("local", rule.get("reason", "Pattern matched"))
    return ("cloud", "No sensitive patterns detected")


def get_client(route: str = "local") -> AsyncOpenAI:
    """Get an OpenAI-compatible async client for the given route.

    URLs and credentials are read from the environment so the same code
    runs on host, in NemoClaw, and in production unchanged.
    """
    if route == "local":
        return AsyncOpenAI(
            base_url=os.environ.get(
                "SOC_CLAW_DOCKER_INFERENCE_URL",
                "http://localhost:8000/v1",
            ),
            api_key=os.environ.get("OPENAI_API_KEY", "not-needed"),
        )

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Cloud route requested but OPENROUTER_API_KEY is not set. "
            "Either set it in your .env / orchestrator secrets, or "
            "constrain the privacy router so no prompt routes to cloud."
        )
    return AsyncOpenAI(
        base_url=os.environ.get(
            "SOC_CLAW_INFERENCE_URL",
            "https://openrouter.ai/api/v1",
        ),
        api_key=api_key,
    )


MODEL_NAME = os.environ.get(
    "SOC_CLAW_LOCAL_MODEL",
    "phi4-mini:3.8b",
)

# Semaphore that limits how many LLM API calls are in-flight to vLLM at once.
# vLLM's continuous batching is most efficient when its request queue is
# saturated — more concurrent calls = larger batches = better GPU utilisation.
# Tune via SOC_CLAW_LLM_CONCURRENCY (default 10). This is independent of the
# per-pipeline concurrency used by the harness/server.
_LLM_SEM: asyncio.Semaphore | None = None


def _get_llm_sem() -> asyncio.Semaphore:
    global _LLM_SEM
    if _LLM_SEM is None:
        n = max(1, int(os.environ.get("SOC_CLAW_LLM_CONCURRENCY", "10")))
        _LLM_SEM = asyncio.Semaphore(n)
    return _LLM_SEM


def log_routing_decision(agent_name: str, route: str, reason: str, prompt: str):
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


def guided_json_kwargs(schema_class, route: str) -> dict:
    """Build ``extra_body`` kwargs for vLLM guided-JSON decoding.

    Only applies on the ``local`` route where the backend is vLLM.
    Cloud endpoints (e.g. Nvidia API) don't support the ``guided_json``
    extension, so we return an empty dict and let the caller fall back
    to regex-based ``extract_json`` parsing.
    """
    if route != "local":
        return {}
    return {"extra_body": {"guided_json": schema_class.model_json_schema()}}


def _try_parse(schema_class, content: str) -> dict | None:
    """Attempt Pydantic-validated parse, then regex fallback.

    Returns the validated dict on success, or ``None`` on any failure.
    """
    try:
        return schema_class.model_validate_json(content).model_dump()
    except Exception:
        pass
    try:
        return schema_class.model_validate(extract_json(content)).model_dump()
    except Exception:
        return None


async def call_llm(
    agent_name: str,
    system_prompt: str,
    user_content: str,
    schema_class,
    retry_hint: str,
    default_factory=None,
) -> tuple[dict, int, str, str]:
    """Shared LLM call scaffold used by all three agents.

    Handles: privacy routing → client selection → guided_json kwargs →
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
    tuple of (result_dict, inference_ms, route, raw_content)
        ``result_dict`` has ``_inference_ms``, ``_route``, and
        ``_raw_response`` already attached.
    """
    from soc_claw.telemetry import get_tracer
    tracer = get_tracer()

    with tracer.start_as_current_span(
        "llm.call",
        attributes={"agent": agent_name, "model": MODEL_NAME},
    ) as span:
        # ── Route & client ────────────────────────────────────────
        route, reason = route_request(user_content)
        span.set_attribute("route", route)
        span.set_attribute("route.reason", reason)
        log_routing_decision(agent_name, route, reason, user_content)
        client = get_client(route)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        gj = guided_json_kwargs(schema_class, route)
        llm_sem = _get_llm_sem()

        # ── First call ────────────────────────────────────────────
        # Semaphore limits concurrent in-flight requests to vLLM so its
        # continuous-batching scheduler always has a full queue to work with.
        # Timing is measured inside the semaphore so it reflects actual LLM
        # time, not queue-wait time.
        async with llm_sem:
            inference_start = time.perf_counter()
            response = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                **gj,
            )
            inference_ms = int((time.perf_counter() - inference_start) * 1000)
        log_inference(agent_name, route, inference_ms)

        content = response.choices[0].message.content or ""
        result = _try_parse(schema_class, content)
        used_retry = False
        used_default = False

        # ── Retry once ────────────────────────────────────────────
        if result is None:
            used_retry = True
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": retry_hint})
            async with llm_sem:
                response = await client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=messages,
                    **gj,
                )
            content = response.choices[0].message.content or ""
            result = _try_parse(schema_class, content)

        # ── Default fallback ──────────────────────────────────────
        if result is None:
            used_default = True
            result = default_factory() if default_factory else {}

        span.set_attribute("parse.success", not used_default)
        span.set_attribute("used_retry", used_retry)
        span.set_attribute("used_default", used_default)

        # ── Attach telemetry metadata ─────────────────────────────
        result["_inference_ms"] = inference_ms
        result["_route"] = route
        result["_raw_response"] = content

        return result, inference_ms, route, content

