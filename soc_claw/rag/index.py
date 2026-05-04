import argparse
import json
import os
import re
import time
from pathlib import Path

from soc_claw.rag.embed import embed
from soc_claw.rag.mitre import map_technique_name
from soc_claw.rag.pinecone_client import DEFAULT_INDEX_NAME, ensure_index, get_pinecone_index

_INDEX_DIM = 384

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_JSONL_PATH = _REPO_ROOT / "data" / "incident_response_playbook_dataset.jsonl"


def _looks_like_id(value: str) -> bool:
    return bool(re.match(r"^T\d{4}(?:\.\d{3})?$", value or ""))


def _coerce_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _summarize_steps(steps: list[dict]) -> str:
    parts = []
    for step in steps:
        phase = step.get("phase", "").strip()
        action = step.get("action", "").strip()
        tools = ", ".join(_coerce_list(step.get("tools")))
        if not phase and not action:
            continue
        entry = f"{phase}: {action}" if phase else action
        if tools:
            entry += f" (tools: {tools})"
        parts.append(entry)
    return " | ".join(parts)


def _build_embedding_text(entry: dict) -> str:
    tactics = entry.get("tactics_techniques") or []
    tactic_lines = []
    for tt in tactics:
        tactic = (tt.get("tactic") or "").strip()
        technique = (tt.get("technique") or "").strip()
        if technique and tactic:
            tactic_lines.append(f"{tactic}: {technique}")
        elif technique:
            tactic_lines.append(technique)
    tactics_text = "; ".join(tactic_lines)

    steps_text = _summarize_steps(_coerce_list(entry.get("playbook_steps")))
    tags_text = ", ".join(_coerce_list(entry.get("tags")))

    parts = [
        f"Incident Type: {entry.get('incident_type', '')}",
        f"Target Asset: {entry.get('target_asset', '')}",
        f"Detection Source: {entry.get('detection_source', '')}",
        f"Initial Vector: {entry.get('initial_vector', '')}",
        f"Severity: {entry.get('severity', '')}",
    ]
    if tactics_text:
        parts.append(f"Tactics and Techniques: {tactics_text}")
    if tags_text:
        parts.append(f"Tags: {tags_text}")
    if steps_text:
        parts.append(f"Playbook Steps: {steps_text}")
    return "\n".join(p for p in parts if p.strip())


def _extract_technique_names(entry: dict) -> list[str]:
    names = []
    for tt in _coerce_list(entry.get("tactics_techniques")):
        name = (tt.get("technique") or "").strip()
        if name:
            names.append(name)
    return names


def _map_technique_ids(technique_names: list[str]) -> list[str]:
    ids = []
    for name in technique_names:
        if _looks_like_id(name):
            ids.append(name)
            continue
        mapped = map_technique_name(name)
        if mapped:
            ids.append(mapped)
    return sorted({t for t in ids if t})


def _build_metadata(entry: dict) -> dict:
    incident_id = entry.get("incident_id") or ""
    incident_type = entry.get("incident_type") or ""
    target_asset = entry.get("target_asset") or ""
    title_parts = [p for p in (incident_type, target_asset) if p]
    title = " - ".join(title_parts) or incident_id

    technique_names = _extract_technique_names(entry)
    technique_ids = _map_technique_ids(technique_names)

    snippet = _summarize_steps(_coerce_list(entry.get("playbook_steps")))

    return {
        "playbook_id": incident_id,
        "title": title,
        "incident_type": incident_type,
        "target_asset": target_asset,
        "severity": entry.get("severity"),
        "technique_ids": technique_ids,
        "technique_names": technique_names,
        "tags": _coerce_list(entry.get("tags")),
        "snippet": snippet,
    }


def _iter_playbooks(jsonl_path: Path):
    with open(jsonl_path) as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"skipping malformed line {lineno} of {jsonl_path.name}: {exc}")
                continue


def _get_total_vector_count(stats) -> int:
    if isinstance(stats, dict):
        return int(stats.get("total_vector_count") or 0)
    return int(getattr(stats, "total_vector_count", 0) or 0)


def _wait_for_index(index, timeout_s: int) -> dict:
    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            stats = index.describe_index_stats()
            if stats is not None:
                return stats
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    if last_error:
        raise last_error
    raise RuntimeError("Pinecone index not ready")


def seed_playbook_index(
    jsonl_path: Path | None = None,
    *,
    force: bool = False,
    wait: bool = False,
    wait_timeout: int = 60,
    batch_size: int = 50,
    max_items: int | None = None,
) -> dict:
    jsonl_path = jsonl_path or _DEFAULT_JSONL_PATH
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Playbook dataset not found: {jsonl_path}")

    index_name = os.getenv("PINECONE_INDEX_NAME", DEFAULT_INDEX_NAME)
    ensure_index(index_name, dimension=_INDEX_DIM, metric="cosine")
    index = get_pinecone_index(index_name=index_name)

    stats = _wait_for_index(index, wait_timeout) if wait else index.describe_index_stats()
    total_vectors = _get_total_vector_count(stats)
    if total_vectors > 0 and not force:
        return {"index_name": index_name, "seeded": False, "vector_count": total_vectors}

    vectors = []
    count = 0
    for i, entry in enumerate(_iter_playbooks(jsonl_path)):
        if max_items is not None and i >= max_items:
            break
        playbook_id = entry.get("incident_id") or f"playbook-{i}"
        text = _build_embedding_text(entry)
        values = embed(text)
        if not values:
            continue
        metadata = _build_metadata(entry)
        vectors.append({"id": playbook_id, "values": values, "metadata": metadata})
        if len(vectors) >= batch_size:
            index.upsert(vectors=vectors)
            count += len(vectors)
            vectors = []

    if vectors:
        index.upsert(vectors=vectors)
        count += len(vectors)

    stats = index.describe_index_stats()
    total_vectors = _get_total_vector_count(stats)
    return {"index_name": index_name, "seeded": True, "vector_count": total_vectors, "upserted": count}


def seed_index(*args, **kwargs) -> dict:
    return seed_playbook_index(*args, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Pinecone with IR playbooks")
    parser.add_argument("--jsonl", type=Path, default=_DEFAULT_JSONL_PATH)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--wait-timeout", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-items", type=int, default=None)
    args = parser.parse_args()

    result = seed_playbook_index(
        jsonl_path=args.jsonl,
        force=args.force,
        wait=args.wait,
        wait_timeout=args.wait_timeout,
        batch_size=args.batch_size,
        max_items=args.max_items,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
