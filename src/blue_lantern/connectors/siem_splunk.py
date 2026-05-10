"""Splunk SIEM mapper implementation."""

import logging
from typing import Dict, Any

from blue_lantern.connectors.base import SIEMMapper, NormalizationError

logger = logging.getLogger("blue-lantern.connectors.splunk")


class SplunkMapper(SIEMMapper):
    """Mapper for Splunk alert events."""

    # Field mapping from Splunk to Alert schema
    FIELD_MAPPING = {
        "_time": "timestamp",
        "_raw": "payload",
        "source": "source",
        "sourcetype": "rule_name",
        "host": "hostname",
    }

    # Nested field mappings (dot notation)
    NESTED_MAPPING = {
        "result.source_ip": "source_ip",
        "result.dest_ip": "dest_ip",
        "result.alert_id": "id",
    }

    def _get_nested_value(self, data: Dict[str, Any], path: str) -> Any:
        """Get value from nested dict using dot notation."""
        keys = path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value

    def normalize(self, raw_event: Dict[str, Any]) -> Dict[str, Any]:
        """Transform Splunk event to Alert schema."""
        try:
            alert = {}

            # Map top-level fields
            for splunk_field, alert_field in self.FIELD_MAPPING.items():
                if splunk_field in raw_event:
                    alert[alert_field] = raw_event[splunk_field]

            # Map nested fields
            for splunk_path, alert_field in self.NESTED_MAPPING.items():
                value = self._get_nested_value(raw_event, splunk_path)
                if value is not None:
                    alert[alert_field] = value

            # Set defaults for optional fields
            alert.setdefault("source_ip", None)
            alert.setdefault("dest_ip", None)
            alert.setdefault("payload", "")

            # Strip ground_truth if present (dev-only field)
            alert.pop("ground_truth", None)

            # Validate required fields
            required_fields = ["id", "timestamp", "hostname", "rule_name"]
            missing_fields = [f for f in required_fields if f not in alert or not alert[f]]
            if missing_fields:
                raise NormalizationError(
                    f"Missing required fields: {', '.join(missing_fields)}"
                )

            return alert

        except Exception as e:
            logger.error(f"Splunk normalization failed: {e}")
            raise NormalizationError(f"Splunk normalization failed: {e}")

    def extract_source(self, raw_event: Dict[str, Any]) -> str:
        """Extract Splunk as source identifier."""
        return "splunk"
