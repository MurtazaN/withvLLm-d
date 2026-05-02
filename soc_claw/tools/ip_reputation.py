import json
import logging
from functools import lru_cache
from pathlib import Path

from soc_claw.tools.registry import register

DATA_DIR = Path(__file__).parent.parent / "data"
_logger = logging.getLogger("soc-claw.tools.ip_reputation")


@lru_cache(maxsize=1)
def _load_threat_intel(data_dir: Path | None = None) -> tuple:
    """Load and validate threat intel data. Cached after first call."""
    from soc_claw.schemas import ThreatIntelEntry

    directory = data_dir or DATA_DIR
    with open(directory / "threat_intel.json") as f:
        raw = json.load(f)
    validated = []
    for i, item in enumerate(raw):
        try:
            entry = ThreatIntelEntry.model_validate(item)
            validated.append(entry.model_dump())
        except Exception as exc:
            _logger.warning("Skipping invalid threat_intel entry at index %d: %s", i, exc)
    # lru_cache requires a hashable return type; wrap in tuple.
    return tuple(validated)


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


if __name__ == "__main__":
    # Test with known malicious IP
    result = ip_reputation("185.220.101.42")
    print(f"Known malicious IP: {result}")
    assert result["verdict"] == "malicious"
    assert result["threat_score"] == 95

    # Test with unknown IP
    result = ip_reputation("8.8.8.8")
    print(f"Unknown IP: {result}")
    assert result["verdict"] == "unknown"
    assert result["threat_score"] == 0

    # Test with another known malicious IP
    result = ip_reputation("203.0.113.99")
    print(f"Ransomware IP: {result}")
    assert result["verdict"] == "malicious"
    assert "ransomware-infra" in result["tags"]

    print("\nAll ip_reputation tests passed!")
