import json
import logging
from functools import lru_cache
from pathlib import Path

from soc_claw.tools.registry import register

DATA_DIR = Path(__file__).parent.parent / "data"
_logger = logging.getLogger("soc-claw.tools.asset_lookup")


@lru_cache(maxsize=1)
def _load_asset_inventory(data_dir: Path | None = None) -> tuple:
    """Load and validate asset inventory. Cached after first call."""
    from soc_claw.schemas import Asset

    directory = data_dir or DATA_DIR
    with open(directory / "asset_inventory.json") as f:
        raw = json.load(f)
    validated = []
    for i, item in enumerate(raw):
        try:
            asset = Asset.model_validate(item)
            validated.append(asset.model_dump())
        except Exception as exc:
            _logger.warning("Skipping invalid asset entry at index %d: %s", i, exc)
    return tuple(validated)


def asset_lookup(hostname: str, data_dir: Path | None = None) -> dict:
    """Retrieve asset information from CMDB/inventory."""
    inventory = _load_asset_inventory(data_dir)

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


class AssetLookupTool:
    name = "asset_lookup"
    description = "Provides CMDB asset inventory details including criticality, owner, and business function for hostnames."

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir

    def run(self, alert: dict) -> dict:
        hostname = alert.get("hostname", "")
        if not hostname:
            return {}
        return asset_lookup(hostname, self.data_dir)


register(AssetLookupTool())


