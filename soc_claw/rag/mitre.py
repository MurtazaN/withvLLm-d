import json
import logging
import re
from functools import lru_cache
from pathlib import Path

_logger = logging.getLogger("soc-claw.rag.mitre")

_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
_MITRE_JSONL = _DATA_ROOT / "Mitre_framework_dataset.jsonl"
_MOCK_MITRE = Path(__file__).resolve().parents[1] / "mock_data" / "mitre_techniques.json"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@lru_cache(maxsize=1)
def _load_mitre_rows() -> tuple[dict, ...]:
    rows = []
    if _MITRE_JSONL.exists():
        with open(_MITRE_JSONL) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    _logger.warning("Skipping invalid MITRE line: %s", line[:80])
    return tuple(rows)


@lru_cache(maxsize=1)
def load_mitre_mappings() -> tuple[dict[str, str], dict[str, str]]:
    name_to_id: dict[str, str] = {}
    id_to_name: dict[str, str] = {}
    for row in _load_mitre_rows():
        tech_id = row.get("id") or row.get("technique_id")
        name = row.get("technique") or row.get("name")
        if tech_id and name:
            name_to_id.setdefault(_normalize(name), tech_id)
            id_to_name.setdefault(tech_id, name)

    if _MOCK_MITRE.exists():
        try:
            with open(_MOCK_MITRE) as f:
                for row in json.load(f):
                    tech_id = row.get("technique_id")
                    name = row.get("name")
                    if tech_id and name:
                        name_to_id.setdefault(_normalize(name), tech_id)
                        id_to_name.setdefault(tech_id, name)
        except Exception as exc:
            _logger.warning("Failed to load mock MITRE data: %s", exc)

    return name_to_id, id_to_name


def map_technique_name(name: str) -> str | None:
    name_to_id, _ = load_mitre_mappings()
    norm = _normalize(name)
    if not norm:
        return None
    if norm in name_to_id:
        return name_to_id[norm]
    if ":" in name:
        alt = _normalize(name.split(":", 1)[1])
        if alt in name_to_id:
            return name_to_id[alt]
    return None


def map_technique_id(technique_id: str) -> str | None:
    _, id_to_name = load_mitre_mappings()
    return id_to_name.get(technique_id)
