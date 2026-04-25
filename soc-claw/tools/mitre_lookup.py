import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
_cache = None


def _load_mitre_techniques():
    global _cache
    if _cache is None:
        with open(DATA_DIR / "mitre_techniques.json") as f:
            _cache = json.load(f)
    return _cache


def mitre_lookup(behavior: str) -> list[dict]:
    """Map observed behavior description to MITRE ATT&CK techniques."""
    techniques = _load_mitre_techniques()
    behavior_tokens = set(re.findall(r"[a-z0-9]+", behavior.lower()))

    matches = []
    for tech in techniques:
        keywords = set(tech["keywords"])
        overlap = keywords & behavior_tokens
        if overlap:
            match_score = round(len(overlap) / len(keywords), 2)
            matches.append({
                "technique_id": tech["technique_id"],
                "name": tech["name"],
                "tactic": tech["tactic"],
                "description": tech["description"],
                "match_score": match_score,
            })

    matches.sort(key=lambda x: x["match_score"], reverse=True)
    return matches[:3]


if __name__ == "__main__":
    # Test PowerShell behavior
    result = mitre_lookup("powershell encoded command downloading payload from external IP")
    print(f"PowerShell behavior: {[r['technique_id'] for r in result]}")
    assert any(r["technique_id"] == "T1059.001" for r in result)

    # Test brute force
    result = mitre_lookup("brute force failed login authentication attempts password guessing")
    print(f"Brute force: {[r['technique_id'] for r in result]}")
    assert any(r["technique_id"] == "T1110.001" for r in result)

    # Test no match
    result = mitre_lookup("normal web browsing activity on corporate laptop")
    print(f"Normal activity: {result}")

    # Test DNS tunneling
    result = mitre_lookup("dns tunneling query subdomain exfil covert channel")
    print(f"DNS tunneling: {[r['technique_id'] for r in result]}")
    assert any(r["technique_id"] == "T1071.004" for r in result)

    print("\nAll mitre_lookup tests passed!")
