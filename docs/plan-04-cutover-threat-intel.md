# Plan 04 — Cutover: Threat Intel (VirusTotal)

**Parent:** [production_data_architecture.md](production_data_architecture.md), step 4 (per-source cutover)
**Status:** Draft — step list to flesh out at implementation time once API access is provisioned.

## Goal

Replace the [data/threat_intel_data.json](../data/threat_intel_data.json) loader inside [src/blue_lantern/tools/ip_reputation.py](../src/blue_lantern/tools/ip_reputation.py) with VirusTotal v3 API calls, backed by the cache from [plan-01-cache.md](plan-01-cache.md).

## Scope

- **In:** VirusTotal client, response mapping, cache integration, tool refactor, env vars, tests with VCR cassettes.
- **Out:** Multi-provider routing (MISP / Recorded Future / etc.) — deferred to v2 once a provider abstraction is justified by a real second provider.

## Pinned decisions

| Decision | Choice | Rationale |
| :--- | :--- | :--- |
| Provider | VirusTotal v3 | Most-used IOC API; broad coverage |
| Auth | `VIRUSTOTAL_API_KEY` env var | Mounted as k8s `Secret` in prod |
| Cache TTL | 24h (per Plan 01) | Threat intel changes slowly; aggressive caching protects rate limits |
| Rate limit handling | Exponential backoff with jitter; max 30s wait | VT public tier is 4 req/min; paid tiers higher |
| Quota exhaustion | Return cached value if present; else `{"verdict": "unknown"}` | Pipeline must keep running |
| Response mapping | Map VT response → existing `ip_reputation` return shape | No call-site changes |
| Internal-IP filter | Preserve existing RFC1918 skip in tool wrapper | Don't waste quota on internal IPs |
| Cache key normalization | Canonical IP form (no leading zeros; lowercase IPv6) | Prevents cache misses on equivalent IPs |

## Steps (skeleton — flesh out at implementation)

1. Build `src/blue_lantern/connectors/virustotal.py` — async `httpx` client, retry/backoff, rate-limit aware.
2. Map VT response → existing `ip_reputation` return shape (keep `threat_score`, `tags`, `campaigns`, `first_seen`, `last_seen`, `verdict`).
3. Wrap with `cache.get_or_compute(f"ip_rep:{normalize(ip)}", lookup_fn, 86_400)`.
4. Replace JSON load in `src/blue_lantern/tools/ip_reputation.py` with the connector + cache call. Delete the `_load_threat_intel` helper.
5. Add `VIRUSTOTAL_API_KEY` to `.env.example` + [config-reference.md](config-reference.md).
6. Tests with VCR cassettes (record/replay; CI does not hit live API).

## Acceptance criteria

- `ip_reputation(ip)` returns the same shape as today (no caller changes).
- Cache hit → 0 external calls.
- Cache miss → 1 VT call → cached for 24h.
- Rate limit → exponential backoff; eventually returns or degrades to `"unknown"`.
- Tests pass against recorded cassettes.

## Risks

- **VT free tier quota (500/day)** won't survive prod load → paid tier required, or another provider. Flag in capacity planning before launch.
- **IPv6 handling** needs explicit testing — providers behave differently from IPv4.
- **Cache key drift** if normalization is applied inconsistently. Mitigation: single `normalize_ip()` helper used everywhere the key is built.

## Open questions

- Hash IOCs (file hashes) aren't queried by `ip_reputation`; extending tool coverage to file hashes is its own plan, not this one.
