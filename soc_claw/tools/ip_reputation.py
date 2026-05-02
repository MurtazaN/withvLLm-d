import json
import logging
from functools import lru_cache
from pathlib import Path

from soc_claw.tools.registry import register

DATA_DIR = Path(__file__).parent.parent / "data"
_logger = logging.getLogger("soc-claw.tools.ip_reputation")


from soc_claw.utils import load_validated_json

@lru_cache(maxsize=1)
def _load_threat_intel(data_dir: Path | None = None) -> tuple:
    """Load and validate threat intel data. Cached after first call."""
    from soc_claw.schemas import ThreatIntelEntry

    directory = data_dir or DATA_DIR
    return load_validated_json(directory / "threat_intel.json", ThreatIntelEntry, _logger)


def ip_reputation(ip: str, data_dir: Path | None = None) -> dict:
    """Look up IP address against threat intelligence database."""
    threat_intel = _load_threat_intel(data_dir)

    for entry in threat_intel:
        if entry["type"] == "ip" and entry["indicator"] == ip:
            score = entry["threat_score"]
            if score >= 80:
                verdict = "malicious"
            elif score >= 40:
                verdict = "suspicious"
            elif score > 0:
                verdict = "low_risk"
            else:
                verdict = "unknown"
            return {
                "threat_score": score,
                "tags": entry["tags"],
                "campaigns": entry["campaigns"],
                "first_seen": entry["first_seen"],
                "last_seen": entry["last_seen"],
                "verdict": verdict,
            }

    return {
        "threat_score": 0,
        "tags": [],
        "campaigns": [],
        "first_seen": None,
        "last_seen": None,
        "verdict": "unknown",
    }


class IPReputationTool:
    name = "ip_reputation"
    description = "Provides threat intelligence scores, tags, and known campaigns for IP addresses."

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir

    def run(self, alert: dict) -> dict:
        results = {}
        dest_ip = alert.get("dest_ip")
        if dest_ip:
            results["dest_ip"] = ip_reputation(dest_ip, self.data_dir)
            
        source_ip = alert.get("source_ip")
        if source_ip and not source_ip.startswith("10.") and not source_ip.startswith("192.168."):
            results["source_ip"] = ip_reputation(source_ip, self.data_dir)
            
        return results


register(IPReputationTool())


