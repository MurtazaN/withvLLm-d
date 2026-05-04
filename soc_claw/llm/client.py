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
    return AsyncOpenAI(base_url=base_url, api_key=api_key)


def select_endpoint(agent: str, prompt: str) -> tuple[AsyncOpenAI, str, str, str]:
    """Return (client, model_name, provider_name, reason) for the given agent and prompt.

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
        return _client_for(cfg, force_provider), force_model, force_provider, "force override"

    # 2 — Content-based rule (first match wins)
    for rule in cfg["content_routes"]:
        if re.search(rule["when"], prompt):
            return _client_for(cfg, rule["provider"]), rule["model"], rule["provider"], f"content: {rule['when']}"

    # 3 — Agent default
    agent_cfg = cfg["agents"][agent]
    provider = agent_cfg["provider"]
    model = agent_cfg["model"]
    return _client_for(cfg, provider), model, provider, f"agent default: {agent}"


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
