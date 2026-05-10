# Plan 02 — Bounded RAG over Pinecone

**Parent:** [production_data_architecture.md](production_data_architecture.md), step 3
**Status:** Draft

## Goal

Index `data/incident_response_playbook_dataset.jsonl` and serve top-3 playbook snippets to the response agent, keyed on the alert's mapped ATT&CK technique. Identical code in dev (Pinecone Local via Docker) and prod (managed Pinecone on GCP).

## Scope

- **In:** Embedding pipeline, indexing module, retrieval module, response-agent wiring, Pinecone Local Compose service, env vars, tests.
- **Out:** Reranking, multi-hop / agentic retrieval, response-agent prompt redesign. All deferred.

## Pinned decisions

| Decision | Choice | Rationale |
| :--- | :--- | :--- |
| Vector DB | Pinecone | Production-ready managed; Docker emulator for dev parity |
| Embedding model | `BAAI/bge-small-en-v1.5` (384-dim) | CPU-friendly, runs alongside vLLM, good quality/cost ratio |
| Embedding strategy | Client-side, both dev and prod | Pinecone Inference unavailable in Pinecone Local; pick once, parity preserved |
| Index dimension / metric | 384 / cosine | Matches embedding model |
| Retrieval shape | top-3, single-pass, no re-query | Bounded RAG per parent doc |
| Retrieval key | ATT&CK technique IDs from triage | Keeps response stage planner-only |
| Multi-technique alerts | Union top-3 across techniques, dedupe, then top-3 by score | Avoid bias toward first technique listed |
| Chunk unit | One playbook entry per chunk | Playbooks are short structured docs; over-chunking adds noise |
| Index seeding | On container start, idempotent (skip if populated) | Pinecone Local is in-memory; restarts are routine |

## Steps

1. Inspect [data/incident_response_playbook_dataset.jsonl](../data/incident_response_playbook_dataset.jsonl) — pick text fields for embedding vs. fields kept as metadata (`technique_ids`, `playbook_id`, `title`).
2. Add deps to `pyproject.toml`: `pinecone>=6`, `sentence-transformers`.
3. `src/blue_lantern/rag/embed.py` — load embedding model once at startup; expose `embed(text: str) -> list[float]`.
4. `src/blue_lantern/rag/index.py` — read JSONL, embed each entry, upsert with metadata. Idempotent: check index stats before seeding.
5. `src/blue_lantern/rag/retrieve.py` — `retrieve(technique_ids: list[str], top_k=3) -> list[Playbook]`, using Pinecone metadata filter on `technique_ids`.
6. Add Pinecone Local service to `docker-compose.yml`. Compose entrypoint runs `index.py` after Pinecone is healthy.
7. Wire retrieval into the response agent — replace any current static playbook lookup.
8. Env vars: `PINECONE_API_KEY`, `PINECONE_HOST`, `PINECONE_INDEX_NAME`. Add to `.env.example` + [config-reference.md](config-reference.md).
9. `tests/test_rag.py`: seeding idempotency, retrieval-with-filter, response-agent integration with mocked Pinecone.

## Acceptance criteria

- `docker compose up` brings up Pinecone Local and seeds the index.
- `retrieve(["T1059.001"])` returns 3 relevant playbooks.
- Response agent receives retrieved snippets in its context.
- Production cutover = `PINECONE_HOST` env-var change only; no code change.
- `tests/test_rag.py` passes against Pinecone Local in CI.

## Risks

- **Image size:** embedding model adds ~400 MB. Acceptable; cache the model layer in Docker build.
- **Cold-start re-seed latency:** ~hundreds of entries × ~50 ms embed each ≈ a few seconds. Acceptable.
- **Coverage gaps:** if the playbook dataset doesn't cover an alert's technique, response agent must degrade to analyst-only mode (no playbook context).
- **Pinecone Local restart wipes data:** known limitation; mitigated by idempotent re-seeding.

## Open questions

- Async retrieval in parallel with verifier work — defer until measurement shows it matters.
- Reranking layer — skip for v1; revisit if relevance complaints surface.
