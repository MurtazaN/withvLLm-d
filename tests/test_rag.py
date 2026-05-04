import os
import socket
from urllib.parse import urlparse

import pytest

from soc_claw.agents import response_agent
from soc_claw.llm.caller import LLMResult
from soc_claw.rag.index import seed_playbook_index
from soc_claw.rag.retrieve import retrieve


def _pinecone_reachable() -> bool:
    host = os.getenv("PINECONE_HOST")
    if not host:
        return False
    if "://" not in host:
        host = f"http://{host}"
    parsed = urlparse(host)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _pinecone_reachable(), reason="Pinecone not reachable")
def test_seed_idempotent():
    first = seed_playbook_index(force=True, wait=True)
    second = seed_playbook_index(force=False, wait=False)
    assert first["vector_count"] >= 1
    assert second["seeded"] is False
    assert second["vector_count"] >= 1


@pytest.mark.skipif(not _pinecone_reachable(), reason="Pinecone not reachable")
def test_retrieve_with_filter():
    seed_playbook_index(force=False, wait=True)
    results = retrieve(["T1059.001"], top_k=3)
    assert results
    assert len(results) <= 3
    assert all("T1059.001" in r.get("technique_ids", []) for r in results)


@pytest.mark.asyncio
async def test_response_agent_includes_playbooks(monkeypatch):
    playbooks = [
        {
            "playbook_id": "IR-TEST-0001",
            "title": "PowerShell Abuse - Windows Endpoint",
            "technique_ids": ["T1059.001"],
            "snippet": "Identification: Inspect PowerShell logs | Containment: Isolate host",
            "score": 0.91,
        }
    ]

    async def fake_call_llm(*, system_prompt, user_content, **kwargs):
        assert "PLAYBOOK SNIPPETS" in user_content
        result = {
            "alert_id": "ALT-001",
            "severity_acted_on": "P2",
            "was_adjusted": False,
            "response_plan": [],
            "incident_summary": "Test summary",
            "analyst_notes": "",
            "estimated_mttr_impact": "",
        }
        return LLMResult(result=result, inference_ms=0, route="test", raw_content="{}")

    def fake_retrieve(*_args, **_kwargs):
        return playbooks

    monkeypatch.setattr(response_agent, "call_llm", fake_call_llm)
    monkeypatch.setattr(response_agent, "retrieve", fake_retrieve)

    alert = {"id": "ALT-001", "hostname": "host-01"}
    verdict = {"verified_severity": "P2", "mitre_techniques": ["T1059.001"]}

    result = await response_agent.run_response(alert, verdict)
    assert result.get("playbook_snippets") == playbooks
