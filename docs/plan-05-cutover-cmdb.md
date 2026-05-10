# Plan 05 — Cutover: CMDB (ServiceNow)

**Parent:** [production_data_architecture.md](production_data_architecture.md), step 4 (per-source cutover)
**Status:** Draft — step list to flesh out at implementation time once a target ServiceNow instance + OAuth app is provisioned.

## Goal

Replace the [data/asset_inventory_data.json](../data/asset_inventory_data.json) loader inside [src/blue_lantern/tools/asset_lookup.py](../src/blue_lantern/tools/asset_lookup.py) with ServiceNow CMDB Table API calls, backed by the cache from [plan-01-cache.md](plan-01-cache.md).

## Scope

- **In:** ServiceNow client, OAuth2 client-credentials flow, CI mapping, cache integration, tool refactor, env vars, tests with VCR cassettes.
- **Out:** Multi-CMDB support (AWS Config / Azure Resource Graph / Device42 / Jamf) — deferred to v2.

## Pinned decisions

| Decision | Choice | Rationale |
| :--- | :--- | :--- |
| Provider | ServiceNow Table API (`/api/now/table/cmdb_ci_server`) | Most common enterprise CMDB |
| Auth | OAuth2 client credentials | More secure than basic auth; standard ServiceNow integration pattern |
| Env vars | `SERVICENOW_INSTANCE`, `SERVICENOW_CLIENT_ID`, `SERVICENOW_CLIENT_SECRET` | Mounted as k8s `Secret`s |
| Cache TTL | 1h (per Plan 01) | CMDB changes are infrequent but matter (criticality flips) |
| CI mapping | ServiceNow CI fields → existing `Asset` schema | No call-site changes |
| Lookup key | hostname (uppercased — existing behavior) | Match current `asset_lookup` semantics |
| Not-found case | Return existing fallback shape (`criticality: medium`, etc.) | Preserve current degradation behavior |
| Token refresh | Auto-refresh on 401; retry once before failing | Standard OAuth client behavior |

## Steps (skeleton — flesh out at implementation)

1. Build `src/blue_lantern/connectors/servicenow.py` — async `httpx` client, OAuth2 token mgmt with auto-refresh.
2. Map ServiceNow CI → existing `Asset` schema. Field mapping kept in a single module so it's easy to override per-customer.
3. Wrap with `cache.get_or_compute(f"asset:{hostname.upper()}", lookup_fn, 3_600)`.
4. Replace JSON load in `src/blue_lantern/tools/asset_lookup.py` with the connector + cache call. Delete the `_load_asset_inventory` helper.
5. Add `SERVICENOW_*` env vars to `.env.example` + [config-reference.md](config-reference.md).
6. Tests with VCR cassettes (record/replay).

## Acceptance criteria

- `asset_lookup(hostname)` returns the same shape as today.
- Cache hit → 0 external calls.
- Cache miss → 1 ServiceNow query (+ auth call if token expired).
- Token refresh handled transparently.
- Connection failure → cached value if present, else default `Asset` shape.
- Tests pass against recorded cassettes.

## Risks

- **Schema variance** across ServiceNow deployments (custom CI tables, custom fields). Mitigation: keep field mapping in a single module so it's easy to override per-customer.
- **Hostname casing** inconsistencies between alerts and CMDB records. Existing `.upper()` handles most; document the assumption.
- **OAuth token expiry mid-request** → connector retries once with a fresh token before surfacing the error.
- **Rate limits** vary per ServiceNow plan. Cache absorbs most pressure; monitor 429s in prod.

## Open questions

- Some orgs query CMDB by IP rather than hostname. Out of scope for v1; revisit if a customer requires it.
- Multi-environment CMDBs (separate dev/prod ServiceNow instances): handle via env-keyed config, not connector logic.
