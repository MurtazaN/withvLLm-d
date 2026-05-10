"""CrowdStrike SIEM mapper implementation."""

import logging
from typing import Dict, Any

from blue_lantern.connectors.base import SIEMMapper, NormalizationError

logger = logging.getLogger("blue-lantern.connectors.crowdstrike")


class CrowdStrikeMapper(SIEMMapper):
    """Mapper for CrowdStrike alert events."""

    # Field mapping from CrowdStrike to Alert schema
    FIELD_MAPPING = {
        "detection_id": "id",
        "timestamp": "timestamp",
    }

    # Nested field mappings
    NESTED_MAPPING = {
        "composite.hostname": "hostname",
        "composite.source_ip": "source_ip",
    }

    # Severity mapping to P1-P4
    SEVERITY_MAPPING = {
        "critical": "P1",
        "high": "P2",
        "medium": "P3",
        "low": "P4",
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
        """Transform CrowdStrike event to Alert schema."""
        try:
            alert = {}

            # Map top-level fields
            for cs_field, alert_field in self.FIELD_MAPPING.items():
                if cs_field in raw_event:
                    alert[alert_field] = raw_event[cs_field]

            # Map nested fields
            for cs_path, alert_field in self.NESTED_MAPPING.items():
                value = self._get_nested_value(raw_event, cs_path)
                if value is not None:
                    alert[alert_field] = value

            # Map severity if present
            if "severity" in raw_event:
                cs_severity = raw_event["severity"].lower()
                alert["severity"] = self.SEVERITY_MAPPING.get(cs_severity, "P3")

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
                # CrowdStrike may not have rule_name in the same format
                # Use detection_id as fallback
                if "rule_name" in missing_fields and "id" in alert:
                    alert["rule_name"] = f"CrowdStrike Detection {alert['id']}"
                    missing_fields.remove("rule_name")

                if missing_fields:
                    raise NormalizationError(
                        f"Missing required fields: {', '.join(missing_fields)}"
                    )

            return alert

        except Exception as e:
            logger.error(f"CrowdStrike normalization failed: {e}")
            raise NormalizationError(f"CrowdStrike normalization failed: {e}")

    def extract_source(self, raw_event: Dict[str, Any]) -> str:
        """Extract CrowdStrike as source identifier."""
        return "crowdstrike"
