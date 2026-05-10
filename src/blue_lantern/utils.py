"""Blue Lantern utilities — backward-compatibility re-export shim.

After the SRP refactor (see docs/CLEAN_CODE_SOLID_REVIEW.md, S1), the
functionality that lived here has been split into focused modules:

- ``blue_lantern.llm``       — LLM client, call scaffold, JSON extraction
- ``blue_lantern.config.routing``   — privacy-aware inference routing
- ``blue_lantern.observability.audit``     — structured audit logging helpers

This file re-exports every public name so existing ``from blue_lantern.utils
import X`` statements continue to work unchanged.  New code should import
from the canonical module directly.
"""

# ── Re-exports from blue_lantern.llm ──────────────────────────────────
from blue_lantern.llm.json_extract import extract_json  # noqa: F401
from blue_lantern.llm.client import (  # noqa: F401
    select_endpoint,
    guided_json_kwargs,
)
from blue_lantern.llm.caller import call_llm  # noqa: F401

# ── Re-exports from blue_lantern.config.routing ──────────────────────────────
from blue_lantern.config.routing import (  # noqa: F401
    load_privacy_routes,
    route_request,
)

# ── Re-exports from blue_lantern.observability.audit ────────────────────────────────
from blue_lantern.observability.audit import (  # noqa: F401
    log_analyst_action,
    log_inference,
    log_response_plan,
    log_routing_decision,
    log_tool_call,
    log_verification,
)

import json
import logging
from pathlib import Path


def load_validated_json(path: Path, schema, logger: logging.Logger) -> tuple:
    """Load JSON from path, validate each element against schema, and return tuple."""
    with open(path) as f:
        raw = json.load(f)
    out = []
    for i, item in enumerate(raw):
        try:
            out.append(schema.model_validate(item).model_dump())
        except Exception as e:
            logger.warning("Skipping invalid entry at index %d: %s", i, e)
    return tuple(out)
