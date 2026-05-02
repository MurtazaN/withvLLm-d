"""SOC-Claw utilities — backward-compatibility re-export shim.

After the SRP refactor (see docs/CLEAN_CODE_SOLID_REVIEW.md, S1), the
functionality that lived here has been split into focused modules:

- ``soc_claw.llm``       — LLM client, call scaffold, JSON extraction
- ``soc_claw.routing``   — privacy-aware inference routing
- ``soc_claw.audit``     — structured audit logging helpers

This file re-exports every public name so existing ``from soc_claw.utils
import X`` statements continue to work unchanged.  New code should import
from the canonical module directly.
"""

# ── Re-exports from soc_claw.llm ──────────────────────────────────
from soc_claw.llm.json_extract import extract_json  # noqa: F401
from soc_claw.llm.client import (  # noqa: F401
    select_endpoint,
    guided_json_kwargs,
)
from soc_claw.llm.caller import call_llm  # noqa: F401

# ── Re-exports from soc_claw.routing ──────────────────────────────
from soc_claw.routing import (  # noqa: F401
    load_privacy_routes,
    route_request,
)

# ── Re-exports from soc_claw.audit ────────────────────────────────
from soc_claw.audit import (  # noqa: F401
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
