"""Registry for dynamically loaded enrichment tools."""

from blue_lantern.tools.base import EnrichmentTool

_REGISTRY: list[EnrichmentTool] = []


def register(tool: EnrichmentTool) -> None:
    """Register an enrichment tool for use in the triage pipeline."""
    _REGISTRY.append(tool)


def get_all() -> list[EnrichmentTool]:
    """Get all registered enrichment tools."""
    return list(_REGISTRY)
