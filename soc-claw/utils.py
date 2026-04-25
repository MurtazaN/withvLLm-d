import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from openai import AsyncOpenAI

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

    raise ValueError(f"Could not extract valid JSON from LLM response: {text[:200]}...")


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
    """Get an OpenAI-compatible async client for the given route."""
    if route == "local":
        return AsyncOpenAI(
            base_url="http://localhost:8000/v1",
            api_key="not-needed",
        )
    else:
        return AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key="not-needed",
        )


MODEL_NAME = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4"


def log_routing_decision(agent_name: str, route: str, reason: str, prompt: str):
    """Log a privacy routing decision."""
    prompt_hash = hashlib.sha256(prompt[:500].encode()).hexdigest()[:12]
    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info(f"{timestamp} | {route} | {reason} | {prompt_hash} | agent={agent_name}")


def log_tool_call(tool_name: str, tool_input: dict, tool_output: dict, latency_ms: int):
    """Log a tool call with timing."""
    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info(
        f"{timestamp} | tool_call | {tool_name} | input={json.dumps(tool_input)[:200]} | "
        f"latency={latency_ms}ms"
    )


def log_inference(agent_name: str, route: str, latency_ms: int):
    """Log an inference request."""
    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info(f"{timestamp} | inference | {agent_name} | route={route} | latency={latency_ms}ms")


def log_verification(alert_id: str, original: str, verified: str, decision: str, issues: list):
    """Log a verification decision."""
    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info(
        f"{timestamp} | verification | {alert_id} | {original}->{verified} | "
        f"decision={decision} | issues={issues}"
    )


def log_response_plan(alert_id: str, num_steps: int, action_types: list, approval_count: int):
    """Log a response plan."""
    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info(
        f"{timestamp} | response_plan | {alert_id} | steps={num_steps} | "
        f"actions={action_types} | approval_required={approval_count}"
    )


def log_analyst_action(alert_id: str, action: str, details: str):
    """Log an analyst action (approve/reject/steer)."""
    timestamp = datetime.now(timezone.utc).isoformat()
    logger.info(f"{timestamp} | analyst | {alert_id} | {action} | {details}")
