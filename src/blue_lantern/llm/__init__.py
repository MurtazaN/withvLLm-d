"""Blue Lantern LLM subsystem.

Provides the shared LLM call scaffold, OpenAI-compatible client factory,
guided-JSON helpers, and JSON extraction utilities.

Public API (re-exported here for convenience)::

    from blue_lantern.llm import call_llm, select_endpoint, extract_json
"""

from blue_lantern.llm.caller import call_llm
from blue_lantern.llm.client import select_endpoint, guided_json_kwargs
from blue_lantern.llm.json_extract import extract_json

__all__ = [
    "call_llm",
    "extract_json",
    "select_endpoint",
    "guided_json_kwargs",
]
