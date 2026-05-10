"""Privacy-aware inference routing for Blue Lantern.

Determines whether an LLM inference request should be routed to the
local vLLM endpoint or to the cloud, based on regex pattern matching
against the prompt content.  The routing policy is loaded from
``blue_lantern/config/privacy_routes.yaml`` (cached after first read).
"""

import re
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent


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
