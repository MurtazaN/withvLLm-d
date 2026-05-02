"""Robustly extract JSON from LLM response text.

Handles markdown fences, bare JSON, and regex fallback.  Used by
``soc_claw.llm.caller._parse_llm_output`` and available as a standalone
utility for any consumer that needs to pull structured data from
free-text LLM output.
"""

import json
import logging
import re

logger = logging.getLogger("soc-claw.llm.json_extract")


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
