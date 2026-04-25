import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
_cache = None


def _load_threat_intel():
    global _cache
    if _cache is None:
        with open(DATA_DIR / "threat_intel.json") as f:
            _cache = json.load(f)
    return _cache


def ip_reputation(ip: str) -> dict:
    """Look up IP address against threat intelligence database."""
    threat_intel = _load_threat_intel()

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
