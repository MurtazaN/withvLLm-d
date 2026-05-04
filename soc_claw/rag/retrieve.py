import logging

from soc_claw.rag.embed import embed
from soc_claw.rag.mitre import map_technique_id
from soc_claw.rag.pinecone_client import get_pinecone_index

_logger = logging.getLogger("soc-claw.rag.retrieve")


def _matches_from_response(response) -> list:
    if response is None:
        return []
    if isinstance(response, dict):
        return response.get("matches", []) or []
    return getattr(response, "matches", []) or []


def retrieve(technique_ids: list[str], top_k: int = 3) -> list[dict]:
    if not technique_ids:
        return []
    if top_k <= 0:
        return []

    try:
        index = get_pinecone_index()
    except Exception as exc:
        _logger.warning("Pinecone not configured: %s", exc)
        return []

    results: dict[str, dict] = {}

    for technique_id in technique_ids:
        if not technique_id:
            continue
        query_text = map_technique_id(technique_id) or technique_id
        vector = embed(query_text)
        if not vector:
            continue
        try:
            response = index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                filter={"technique_ids": {"$in": [technique_id]}},
            )
        except Exception as exc:
            _logger.warning("query failed for %s: %s", technique_id, exc)
            continue

        for match in _matches_from_response(response):
            if isinstance(match, dict):
                match_id = match.get("id")
                score = match.get("score")
                metadata = match.get("metadata") or {}
            else:
                match_id = getattr(match, "id", None)
                score = getattr(match, "score", None)
                metadata = getattr(match, "metadata", None) or {}

            playbook_id = metadata.get("playbook_id") or match_id or ""
            if not playbook_id:
                continue

            item = {
                "playbook_id": playbook_id,
                "title": metadata.get("title", playbook_id),
                "technique_ids": metadata.get("technique_ids", []),
                "technique_names": metadata.get("technique_names", []),
                "severity": metadata.get("severity"),
                "snippet": metadata.get("snippet", ""),
                "score": score or 0.0,
            }

            existing = results.get(playbook_id)
            if not existing or item["score"] > existing.get("score", 0):
                results[playbook_id] = item

    ranked = sorted(results.values(), key=lambda x: x.get("score", 0), reverse=True)
    return ranked[:top_k]
