import json
import logging
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
_logger = logging.getLogger("soc-claw.tools.asset_lookup")


@lru_cache(maxsize=1)
def _load_asset_inventory() -> tuple:
    """Load and validate asset inventory. Cached after first call."""
    from soc_claw.schemas import Asset

    with open(DATA_DIR / "asset_inventory.json") as f:
        raw = json.load(f)
    validated = []
    for i, item in enumerate(raw):
        try:
            asset = Asset.model_validate(item)
            validated.append(asset.model_dump())
        except Exception as exc:
            _logger.warning("Skipping invalid asset entry at index %d: %s", i, exc)
    return tuple(validated)


def asset_lookup(hostname: str) -> dict:
    """Retrieve asset information from CMDB/inventory."""
    inventory = _load_asset_inventory()

    for asset in inventory:
        if asset["hostname"].upper() == hostname.upper():
            return {**asset, "found": True}

    return {
        "hostname": hostname,
        "criticality": "medium",
        "business_function": "Unknown",
        "owner": "Unknown",
        "os": "Unknown",
        "last_patch": "Unknown",
        "network_zone": "Unknown",
        "found": False,
        "note": "Unknown asset - defaulting to medium criticality",
    }


if __name__ == "__main__":
    # Test known hostname
    result = asset_lookup("DC-FINANCE-01")
    print(f"Known host: {result}")
    assert result["found"] is True
    assert result["criticality"] == "critical"

    # Test case-insensitive
    result = asset_lookup("dc-finance-01")
    print(f"Case-insensitive: {result}")
    assert result["found"] is True

    # Test unknown hostname
    result = asset_lookup("UNKNOWN-HOST-999")
    print(f"Unknown host: {result}")
    assert result["found"] is False
    assert result["criticality"] == "medium"

    print("\nAll asset_lookup tests passed!")
