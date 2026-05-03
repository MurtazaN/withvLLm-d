import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from soc_claw.tools.registry import register

DATA_DIR = Path(__file__).parent.parent / "mock_data"
_logger = logging.getLogger("soc-claw.tools.mitre_lookup")


from soc_claw.utils import load_validated_json

@lru_cache(maxsize=1)
def _load_mitre_techniques(data_dir: Path | None = None) -> tuple:
    """Load and validate MITRE techniques. Cached after first call."""
    from soc_claw.schemas import MitreTechnique

    directory = data_dir or DATA_DIR
    return load_validated_json(directory / "mitre_techniques.json", MitreTechnique, _logger)


def mitre_lookup(behavior: str, data_dir: Path | None = None) -> list[dict]:
    """Map observed behavior description to MITRE ATT&CK techniques."""
    techniques = _load_mitre_techniques(data_dir)
    behavior_tokens = set(re.findall(r"[a-z0-9.]+", behavior.lower()))

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


class MitreLookupTool:
    name = "mitre_lookup"
    description = "Maps observed behavior (rules, payloads) to MITRE ATT&CK techniques with descriptions and tactics."

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir

    def run(self, alert: dict) -> list[dict]:
        behavior = f"{alert.get('rule_name', '')} {alert.get('payload', '')}".strip()
        if not behavior:
            return []
        return mitre_lookup(behavior, self.data_dir)


register(MitreLookupTool())


