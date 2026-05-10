# Plan 01 — Cache Interface + Redis Backend

**Parent:** [production_data_architecture.md](production_data_architecture.md), steps 1 + 2
**Status:** Draft

## Goal

Provide a single cache abstraction used by enrichment tools that hit external APIs. In-memory backend in Phase 1; Redis backend in Phase 2 selected by env var. Same call-site code on both backends.

## Scope

- **In:** `src/blue_lantern/cache.py` module — `Cache` Protocol, `InMemoryCache`, `RedisCache`, factory. Unit + integration tests.
- **Out:** Wrapping the three existing tool files. That happens at cutover (plans 03–05) once each source moves off mock JSON. Pre-cutover, the cache module sits ready but unused.

## Pinned decisions

| Decision | Choice | Rationale |
| :--- | :--- | :--- |
| Interface surface | `get_or_compute(key, compute, ttl_seconds) -> T` | One method covers every call site; eliminates miss-and-set bug class |
| Key format | `f"{namespace}:{value}"` (e.g., `ip_rep:1.2.3.4`) | Namespace-by-source for future invalidation |
| Default TTLs | Threat intel: 24h. Asset: 1h. MITRE: not cached. | Match upstream change rates; avoid caching local-only data |
| Backend selection | `BLUE_LANTERN_REDIS_URL` env var | Empty → InMemory; set → Redis. Already documented in [config-reference.md](config-reference.md) |
| Negative results | Cached as normal sentinel dicts (e.g., `{"verdict": "unknown"}`) | Avoid re-querying for known-empty results inside TTL |
| Stampede protection | None in v1 | Defer until measurement shows it matters |
| Serialization | JSON only (Redis backend `json.dumps` / `json.loads`) | Forces dict-shaped values; rejects accidental complex objects |

## Steps

### Phase 1 — Pre-Compose (in-memory)

1. Add `cachetools` to `pyproject.toml`.
2. Create `src/blue_lantern/cache.py`:
   - `Cache` Protocol with single `get_or_compute` method.
   - `InMemoryCache` wrapping `cachetools.TTLCache` (per-key TTL via expiry timestamps).
   - `get_cache()` factory reading `BLUE_LANTERN_REDIS_URL`.
3. Add `tests/test_cache.py`:
   - Miss → compute called once → result returned and stored.
   - Hit within TTL → compute not called.
   - Expiry after TTL → compute called again.
   - Negative-sentinel result cached and returned within TTL.

### Phase 2 — Pre-k8s (Redis)

4. Add `redis>=5` to `pyproject.toml`.
5. Add `RedisCache` class to `src/blue_lantern/cache.py`, same Protocol. Use `SETEX` for TTL.
6. Update factory: `BLUE_LANTERN_REDIS_URL` set → return `RedisCache`.
7. Add Redis service to `docker-compose.yml` (port 6379, no persistence).
8. Add `tests/test_cache_redis.py` (skip if Redis unavailable):
   - Same scenarios as in-memory, against a real Redis container in CI.
9. Document failure semantics: Redis connection drop → log + fall through to direct `compute()`, do not crash, do not cache the result of that call.

## Acceptance criteria

- `from blue_lantern.cache import get_cache` returns the right backend per env.
- TTLs respected for both backends.
- All tests pass; CI runs Redis tests against a service container.
- No public API changes to `src/blue_lantern/tools/*` yet — that is cutover work.

## Risks

- **Serialization mismatch** with Redis if non-JSON-able values are cached. Mitigation: enforce JSON-serializable values; fail loudly on `set`.
- **Connection drops** mid-pipeline. Mitigation: retry once with short timeout, then degrade to direct compute (no cache write on failure).
- **Cache poisoning** if upstream returns transient garbage and we cache it for 24h. Mitigation: only cache results that pass the existing pydantic schema.
