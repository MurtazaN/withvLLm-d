"""Microsoft Sentinel SIEM mapper implementation."""

import logging
from typing import Dict, Any

from blue_lantern.connectors.base import SIEMMapper, NormalizationError

logger = logging.getLogger("blue-lantern.connectors.sentinel")


class SentinelMapper(SIEMMapper):
    """Mapper for Microsoft Sentinel alert events."""

    # Field mapping from Sentinel to Alert schema
    FIELD_MAPPING = {
        "properties.alertDisplayName": "rule_name",
        "properties.startTimeUtc": "timestamp",
        "systemAlertId": "id",
    }

    # Nested field mappings
    NESTED_MAPPING = {
        "entities.host.name": "hostname",
        "entities.ipAddress.address": "source_ip",
    }

    def _get_nested_value(self, data: Dict[str, Any], path: str) -> Any:
        """Get value from nested dict using dot notation."""
        keys = path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and key.isdigit():
                # Handle array indexing
                idx = int(key)
                if 0 <= idx < len(value):
                    value = value[idx]
                else:
                    return None
            else:
                return None
        return value

    def _get_entity_value(self, entities: list, kind: str, property_name: str) -> Any:
        """Get value from entities array by kind and property name.

        Args:
            entities: List of entity objects
            kind: Entity kind (e.g., "Host", "Ip")
            property_name: Property name to extract (e.g., "hostName", "address")

        Returns:
            Property value or None
        """
        if not entities:
            return None

        for entity in entities:
            if isinstance(entity, dict):
                entity_kind = entity.get("kind")
                if entity_kind == kind:
                    properties = entity.get("properties", {})
                    if isinstance(properties, dict):
                        return properties.get(property_name)

        return None

    def normalize(self, raw_event: Dict[str, Any]) -> Dict[str, Any]:
        """Transform Sentinel event to Alert schema."""
        try:
            alert = {}

            # Map nested fields
            for sentinel_path, alert_field in self.FIELD_MAPPING.items():
                value = self._get_nested_value(raw_event, sentinel_path)
                if value is not None:
                    alert[alert_field] = value

            # Map entity fields using array lookup
            entities = raw_event.get("entities", [])
            if entities:
                # Get hostname from Host entity
                hostname = self._get_entity_value(entities, "Host", "hostName")
                if hostname:
                    alert["hostname"] = hostname

                # Get source_ip from Ip entity
                source_ip = self._get_entity_value(entities, "Ip", "address")
                if source_ip:
                    alert["source_ip"] = source_ip

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
            logger.error(f"Sentinel normalization failed: {e}")
            raise NormalizationError(f"Sentinel normalization failed: {e}")

    def extract_source(self, raw_event: Dict[str, Any]) -> str:
        """Extract Sentinel as source identifier."""
        return "sentinel"
