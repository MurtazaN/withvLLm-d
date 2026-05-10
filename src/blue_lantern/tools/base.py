"""Base protocol for Blue Lantern enrichment tools."""

from typing import Protocol, Any


class EnrichmentTool(Protocol):
    """Protocol that all enrichment tools must implement to be registered."""
    
    name: str
    description: str
    
    def run(self, alert: dict) -> Any:
        """Run the tool against the provided alert.
        
        The tool is responsible for extracting the necessary fields from the
        alert. If the alert lacks the required fields, the tool should
        return a safe empty value (e.g., an empty dict or list).
        """
        ...
