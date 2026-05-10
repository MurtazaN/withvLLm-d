"""Base interfaces and exceptions for SIEM connectors."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any


class ErrorType(Enum):
    """Error classification for DLQ entries."""
    INVALID_JSON = "invalid_json"
    SCHEMA_VALIDATION = "schema_validation"
    NORMALIZATION_FAILURE = "normalization"
    IDEMPOTENCY_CHECK = "idempotency"
    HMAC_VERIFICATION = "hmac_verification"
    PIPELINE_TIMEOUT = "pipeline_timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"


class NormalizationError(Exception):
    """Raised when SIEM event normalization fails."""
    pass


class SIEMMapper(ABC):
    """Abstract base class for SIEM-specific mappers."""

    @abstractmethod
    def normalize(self, raw_event: Dict[str, Any]) -> Dict[str, Any]:
        """Transform SIEM-specific JSON to Alert schema.

        Args:
            raw_event: Raw SIEM event data

        Returns:
            Normalized alert dict matching Alert schema

        Raises:
            NormalizationError: If normalization fails
        """
        pass

    @abstractmethod
    def extract_source(self, raw_event: Dict[str, Any]) -> str:
        """Return SIEM platform identifier for idempotency.

        Args:
            raw_event: Raw SIEM event data

        Returns:
            Source identifier (e.g., "splunk", "sentinel", "crowdstrike")
        """
        pass
