"""OpenAI-compatible async client factory driven by routing.yaml.

Loads provider definitions, per-agent defaults, content-based overrides,
and an optional force block — all from ``soc_claw/config/routing.yaml``.
"""

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

CONFIG_PATH = Path(__file__).parent.parent / "config" / "routing.yaml"

# Per-request timeout for every LLM call. The OpenAI SDK default is 600s,
# which lets a stuck endpoint masquerade as "still working" for 10 minutes.
# 60s is generous for a single chat-completion round trip — even Ollama
# cold-starts on a 7B model finish under that — while failing fast when
# something is genuinely wedged. Override per-deploy via SOC_CLAW_LLM_TIMEOUT.
LLM_TIMEOUT_SECONDS = float(os.environ.get("SOC_CLAW_LLM_TIMEOUT", "180"))


@lru_cache(maxsize=1)
def _load_config() -> dict:
    """Load and cache the routing config from disk."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _client_for(cfg: dict, provider_name: str) -> AsyncOpenAI:
    """Build an AsyncOpenAI client from a provider entry in the config."""
    provider = cfg["providers"][provider_name]
    base_url = provider["base_url"]
    api_key = os.environ.get(provider["api_key_env"], "dummy-key")
    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=LLM_TIMEOUT_SECONDS,
    )


def select_endpoint(agent: str, prompt: str) -> tuple[AsyncOpenAI, str, str]:
    """Return (client, model_name, reason) for the given agent and prompt.

    Resolution order:
      1. ``force`` block
      2. First matching ``content_routes`` rule
      3. Per-agent default from ``agents``
    """
    cfg = _load_config()

    # 1 — Force override
    force_provider = cfg["force"]["provider"]
    force_model = cfg["force"]["model"]
    if force_provider and force_model:
        return _client_for(cfg, force_provider), force_model, "force override"

    # 2 — Content-based rule (first match wins)
    for rule in cfg["content_routes"]:
        if re.search(rule["when"], prompt):
            return _client_for(cfg, rule["provider"]), rule["model"], f"content: {rule['when']}"

    # 3 — Agent default
    agent_cfg = cfg["agents"][agent]
    return _client_for(cfg, agent_cfg["provider"]), agent_cfg["model"], f"agent default: {agent}"


def guided_json_kwargs(schema_class, provider: str) -> dict:
    """Build kwargs for JSON decoding based on the provider capabilities.

    vLLM supports strict `guided_json` schema enforcement.
    Ollama supports basic JSON mode.
    OpenRouter/Cloud endpoints usually don't support extra_body schemas natively in the same way,
    so we return empty or just JSON mode depending on the endpoint.
    """
    if "vllm" in provider:
        return {"extra_body": {"guided_json": schema_class.model_json_schema()}}
    elif "ollama" in provider:
        return {"response_format": {"type": "json_object"}}
    return {}
