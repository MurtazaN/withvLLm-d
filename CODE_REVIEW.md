# SOC-Claw Codebase Review

## Context

Full read-through of the SOC-Claw codebase on branch `feature/llm-d-path-no-claw` after the Docker pivot. The user is building this as a production product, not a hackathon submission — so the review is calibrated to that bar (industry-standard practices, security, observability, testability). 3,913 lines of source across Python (1,673 LoC), data (883 LoC), HTML (640 LoC), and the Gradio UI (709 LoC). Findings below are concrete (file:line where possible) and prioritized.

**Bottom line:** the application logic is cleaner than I expected for something that started as a hackathon. The agents and pipeline are honest — graceful degradation, deterministic enrichment, env-driven configuration. The gaps are concentrated in three places: (1) a handful of real bugs that will misbehave silently, (2) the UI/server has zero authn and a small XSS surface, and (3) production-ready hygiene (pinned deps, tests, observability, schema validation) is missing — all expected after a pivot, all tractable.

---

## Severity legend

- 🔴 **Critical** — security, silent data loss, or correctness bug that affects the demo
- 🟠 **High** — likely to bite in production; fix before shipping
- 🟡 **Medium** — quality / maintainability; fix during normal cleanup
- 🟢 **Low** — style / cosmetic / future-proofing

---

## ✅ What's correct / keep as-is

These are the parts of the codebase that already match production-standard:

1. **`soc-claw/utils.py:91-119` — env-driven `get_client()`.** Single `AsyncOpenAI` surface against any OpenAI-compatible URL. This is exactly the right abstraction layer; it's why I pushed back on adding LangChain.
2. **`utils.py:78-88` — `route_request()` privacy router.** Deterministic, auditable, regex-based. Real differentiator for a security product (no LangChain primitive exists for this).
3. **Agent JSON-fallback pattern.** [triage_agent.py:151-183](soc-claw/agents/triage_agent.py#L151), [verifier_agent.py:123-151](soc-claw/agents/verifier_agent.py#L123), [response_agent.py:142-161](soc-claw/agents/response_agent.py#L142) all retry once with a reminder, then fall back to a *deterministic* default derived from enrichment data (not another LLM call). Honest error handling — many teams ship "retry forever or crash."
4. **`pipeline.py:41-81` — `merge_verdict()`.** Cleanly handles the three verifier decisions (confirmed/adjusted/flagged) with documented invariants.
5. **Per-call telemetry metadata** (`_inference_ms`, `_route`, `_raw_response`, `_tool_calls`) — useful for benchmark analysis and production debugging.
6. **`utils.py:23-54` — `extract_json` regex-then-fallback.** Despite its hackathon shape, the layered approach (markdown strip → direct parse → `{...}` regex → `[...]` regex) covers the common LLM output failure modes. It's the *right* tactical fix; the strategic fix is `guided_json` at decode time.
7. **`Dockerfile` (new) and `.dockerignore`.** Non-root user, dep-layer caching, vLLM intentionally excluded, secrets excluded.
8. **`docker-compose.yml` `extra_hosts: host.docker.internal:host-gateway`.** Correct cross-platform Docker→host wiring.
9. **Three of the four tool modules are pure functions over dict lookups.** [`ip_reputation.py`](soc-claw/tools/ip_reputation.py), [`asset_lookup.py`](soc-claw/tools/asset_lookup.py), and [`mitre_lookup.py`](soc-claw/tools/mitre_lookup.py) are deterministic after cache load — easy to unit-test. **`response_tools.py` is not pure** (`random.randint` for ticket IDs, `datetime.now()`, `print` for stdout side effects), so it needs different test scaffolding (`freezegun`, `random.seed`, `capsys`); see P1.
10. **`benchmark/harness.py:142-178` — verification-improvement metric.** Computing whether an "adjusted" verdict moved the severity *closer* to ground truth (not just changed it) is a non-trivially correct metric.

---

## 🔴 Bugs and correctness issues

### B1 🔴 Silent ticket-priority loss in `pipeline.py:166`
```python
severity = action.get("_severity", "P3")
priority_map = {"P1": "critical", "P2": "high", "P3": "medium", "P4": "low"}
priority = priority_map.get(severity, "medium")
```
The response agent **never sets `_severity` on individual action steps** ([response_agent.py:71-89](soc-claw/agents/response_agent.py#L71) JSON schema), so this `.get("_severity", "P3")` always falls through. Result: every ticket created via analyst approval is tagged `medium` priority regardless of the alert being P1. The `severity_acted_on` field exists on the plan, not on each step. Fix: pass `final_verdict` or `severity_acted_on` into `execute_approved_action()` and use it to derive priority.

### B2 🔴 Digit-starting domains mis-classified as IP in `pipeline.py:158-163`
```python
indicator_type = "ip"
if "." in target and not target[0].isdigit():
    indicator_type = "domain"
elif len(target) == 32 or len(target) == 64:
    indicator_type = "hash"
```
The `not target[0].isdigit()` clause means a domain whose first character is a digit fails the domain check and falls through to the default `"ip"`:
- `"1password.com"` → has `.`, starts with `'1'` → skips `domain` → not 32/64 chars → returns `"ip"`. **Wrong.**
- `"911.gov"` → same path → returns `"ip"`. **Wrong.**

Once `block_ioc` is wired to a real firewall API that has separate code paths for IP vs FQDN, the firewall will reject the input or block the wrong thing.

(IPv6 addresses like `"2001:db8::1"` actually fall through to `"ip"` correctly, since they have no `.` — they're an IP, just v6. So the "IPv6" framing of an earlier draft was incorrect; the digit-domain case is the real bug.)

**Fix:** use stdlib `ipaddress.ip_address(target)` — it raises `ValueError` for anything that isn't a valid v4/v6 address. In the except branch, check for `.` (domain) or fall to length-based hash detection.

### B3 🟠 Tuple-arity mismatch in `triage_agent.py:53-92`
The function signature declares `tuple[dict, dict, list, list]` (4 values) but actually returns 5 (`return ip_result, asset_result, mitre_results, tool_calls_log, source_ip_result`). Caller at line 102 unpacks 5 — code works, type hint and docstring are wrong. Update the signature.

### B4 🟠 `mitre_lookup.py:20` token regex misses dotted technique IDs
`re.findall(r"[a-z0-9]+", behavior.lower())` splits `T1059.001` into two tokens `t1059` and `001`. If `mitre_techniques.json` has `"keywords": ["T1059.001"]` (lowercased to `t1059.001`), the dot-containing keyword will never match. **Action:** read [mitre_techniques.json](soc-claw/data/mitre_techniques.json) and verify the keyword shape; if any keyword contains a `.`, the regex must include it (`r"[a-z0-9.]+"`).

### B5 🟠 `harness.py:165` defensive-parse missing on severity strings
`int(r["triage_severity"][1])` assumes severity strings are always `P[1-4]`. If an LLM response leaks "P1A", "P-1", or "Critical", you get `IndexError` or `ValueError` and the whole benchmark crashes mid-run. Wrap in `try/except`, fall back to skipping the row.

### B6 🟠 `server.py:117` exception swallowed without logging
```python
except Exception as e:
    errors += 1
    results.append({...})  # No log of e
```
Production deployments will be silent on per-alert failures. Add `logger.exception(...)` so the failure shows up in stdout/Loki/Cloudwatch.

### B7 🟡 Stale NemoClaw comments after the pivot
`harness.py:23-28` has "in NemoClaw (where /workspace is readonly), and in production..." — references a runtime that no longer exists. Cosmetic but undermines the cleanup message.

### B8 🟡 `privacy_routes.yaml:21-28` cloud rules are documentation-only
```yaml
cloud_inference:
  route_when:
    - description: "Generic MITRE ATT&CK technique descriptions"
```
`utils.py:84` only iterates `local_inference.route_when`. The cloud `route_when` block is never evaluated — it's aspirational documentation. Either implement (give them `pattern:` keys and an OR-logic in the router) or delete to avoid confusion.

### B9 🟡 `server.py:166-179` `api_override` builds a thin synthetic verdict
The override path passes `{"verified_severity": s, "severity": s, "was_adjusted": True, "was_flagged": False}` into `run_response()`. The response prompt expects verifier reasoning, issues_found, etc. — those are absent, so override-driven plans will be lower-quality than verified-pipeline plans. Document this OR populate the synthetic verdict more fully.

### B10 🟢 `_cache` globals in tools are not async-safe
All three tools use `global _cache; if _cache is None: ...` with no lock. Two coroutines hitting `_load_*()` simultaneously can both pass the `is None` check. Not a *correctness* bug (they write the same dict) but it's a smell. Replace with `@functools.lru_cache(maxsize=1)` on the loader.

### B11 🟢 `MODEL_NAME` evaluated at import time (`utils.py:122`)
```python
MODEL_NAME = os.environ.get("SOC_CLAW_MODEL", "nvidia/Nemotron-Mini-4B-Instruct")
```
This is read once at module import. After Docker startup it never re-reads. Mostly fine for k8s/Docker (env set once at container start), but tests that munge `os.environ` won't see the change without a module reload. Convert to a function `get_model_name()` for cleanliness.

---

## 🔒 Security gaps

### S1 🔴 Zero authentication on the FastAPI server
`server.py` has **no authn at all.** Anyone reachable at `:7860` can list alerts, run the pipeline, override verdicts, and approve "live" actions. For a SOC tool whose response_tools simulate EDR/firewall/ITSM calls, this is a gap that grows from "embarrassing demo" to "audit failure" the moment the tools become real. Add a session/JWT layer (FastAPI `Depends`) before any production deploy; behind a reverse proxy with auth (oauth2-proxy, Clerk) is the lowest-friction path.

### S2 🔴 XSS via LLM output in `templates/index.html`
The exploit chain has four steps:

1. **Attacker plants HTML in an alert.** SIEMs ingest log lines from network devices and endpoints, many of which are partly attacker-controlled (that's why we triage them). An attacker writes a log line like `... note=<img src=x onerror=alert(1)>`; it lands in an alert's `payload` field.
2. **The LLM reads the alert and quotes the payload back.** [response_agent.py:99-117](soc-claw/agents/response_agent.py#L99) feeds the payload into the prompt. Modern LLMs frequently quote suspicious strings into their output — they're trying to flag what was unusual. So `analyst_notes` ends up containing `<img src=x onerror=alert(1)>` verbatim. This is *prompt injection through the data plane*: the attacker isn't attacking the LLM, they're tricking it into copy-pasting attacker-controlled HTML into downstream output.
3. **The frontend renders the LLM output as raw HTML.** [index.html:534](soc-claw/ui/templates/index.html#L534), [index.html:537](soc-claw/ui/templates/index.html#L537):
   ```javascript
   <div ...>${resp.analyst_notes}</div>
   ```
   A template literal interpolates the field directly into the DOM. The browser parses the `<img>` tag, `src=x` fails to load, `onerror` fires.
4. **The script runs in the analyst's session.** With no auth (S1) and no CSRF (S3), the JS can `fetch('/api/approve', ...)` and the server executes the action without any human click.

The same pattern exists in `app.py:382, 387` (Gradio path).

**Fix:** for any field originating from LLM output, build the DOM with `textContent` instead of HTML interpolation:
```javascript
const div = document.createElement('div');
div.textContent = resp.analyst_notes;
container.appendChild(div);
```
The constrained-enum fields (`severity`, `confidence`, `decision`) are safe because they come from a fixed set of strings the schema enforces. Free-text fields (`analyst_notes`, `incident_summary`, `reasoning`, error renderers at lines 399, 413, 595) all need the `textContent` treatment.

### S3 🟠 No CSRF on POST endpoints
`/api/approve`, `/api/override`, `/api/run/{id}`, `/api/run-all` are unprotected POSTs. With S1 absent, a malicious page in a browser tab can cross-site `fetch` against `localhost:7860` (DNS rebinding or shared host). Production-grade: add a CSRF token (FastAPI middleware or `starlette-csrf`) once auth is in place.

### S4 🟠 Approve endpoint trusts the client
`server.py:144-153` `api_approve` accepts whatever `action` JSON the client sends. There's no verification that the action came from a previously-generated response plan, so a client can synthesize `{"action_type": "isolate_host", "target": "DC-FINANCE-01"}` and trigger the response_tool. Today the tool just `print()`s; in production it must hit a real EDR. **Fix:** persist plans server-side (Redis / Postgres) keyed by alert_id, accept only an action_id pointing into a live plan.

### S5 🟡 Two parallel UI implementations, diverging bug-fix histories
`server.py` (FastAPI + `templates/index.html` + Tailwind + vanilla JS) and `app.py` (Gradio) are both **complete backend processes that bundle their own frontend**. Each is a Python HTTP server on `:7860`; only one can run at a time. They call the same pipeline (`run_pipeline`, `execute_approved_action`, `load_alerts`, `get_alert_by_id`, `log_analyst_action`) but render the UI differently and have drifted independently.

What each has that the other lacks:
- **`app.py:467`** sets `action["_severity"] = resp.get("severity_acted_on", "P3")` before calling `execute_approved_action`. This is exactly the missing piece for **B1** (silent ticket-priority loss). `server.py:api_approve` doesn't do this.
- **`app.py:540-611`** streams progress for "run all" via Python generators — the analyst sees per-alert updates as the benchmark runs. `server.py:api_run_all` is single-shot, no progress indication until the whole batch completes.

Real XSS surface in both:
- `app.py:382, 387`: `incident_summary`, `analyst_notes` interpolated as raw HTML (same root cause as S2).
- `app.py:410`: error-message interpolation — marginal risk.
- The fields I initially flagged at `app.py:282-309` (verdict, tags, hostname, technique_id, etc.) come from trusted data files or constrained enums. Safe in the current design.

Production direction is `server.py`, for reasons that don't depend on which UI is "better" today:
1. **Auth is realistic in FastAPI**, awkward in Gradio. FastAPI has `Depends`, middleware, OAuth, sessions. Gradio's auth story is `auth=("user", "pass")` basic-HTTP — fine for hackathons, not for a tool that controls EDR actions.
2. **Security middleware** (CORS, CSRF, rate limiting, security headers) is mature in Starlette/FastAPI. Gradio gives you what it gives you.
3. **Versioning churn.** Gradio has had multiple breaking releases (3 → 4 → 5). FastAPI/Jinja/vanilla JS evolve more slowly.
4. **k8s/llm-d fit.** FastAPI is a stateless HTTP service that fits behind any ingress; the HTML is static. Gradio's stateful queueing model is harder to scale horizontally.

**Resolution sequence (to actually fix this without losing app.py's two good ideas):**
1. Port `_severity` fix from `app.py:467` into `server.py:api_approve`. Closes B1 in the production UI.
2. Port streaming-progress UX into `server.py`. FastAPI supports Server-Sent Events natively (`StreamingResponse` or `sse-starlette`); `api_run_all` becomes an async generator yielding `{"index": i, "alert_id": ..., "status": "running"}` per alert; the frontend opens an `EventSource` and updates a progress bar.
3. Verify both flows in `server.py` match what `app.py` does today.
4. Move `app.py` to `examples/gradio_app.py` (or delete). At that point `server.py` strictly dominates.

If you have ongoing users for both UIs, the alternative is to extract a `soc_claw.web.render` module that returns safely-escaped HTML fragments and have both UIs import it. More architectural work; only earns its keep if both UIs have real users.

### S6 🟡 Hardcoded `Analyst_04` in `index.html:110`
Frontend pretends to know who the analyst is, but the value is a static string. Fine for demo; reflects no real auth. After S1 lands, replace with a Jinja variable from `request.session`.

### S7 🟡 Compose publishes `:7860` on `0.0.0.0`
`docker-compose.yml` `ports: ["7860:7860"]` binds to all interfaces by default. On the Brev VM that's externally reachable from the public IP. Bind to `127.0.0.1:7860:7860` and reach via SSH tunnel until S1 is addressed.

### S8 🟡 No rate limiting / abuse controls
Anyone (after S1 fix, "any logged-in user") can spam `/api/run-all` and saturate the GPU. Production needs `slowapi` or upstream rate limiting at the proxy.

### S9 🟢 `log_inference` doesn't log the prompt hash
`utils.py:128-132` records the prompt SHA-256 for routing decisions (good). `log_inference` only records latency — harder to correlate latency outliers to the prompt that caused them. Add the same hash.

---

## ⚡ Efficiency / structure improvements

### E1 🟠 No concurrency on alert batches
`harness.py:57` and `server.py:92` (`api_run_all`) loop alerts sequentially with `await run_pipeline(alert)`. With async LLM calls this is the canonical place to use `asyncio.gather` with a semaphore. 30 alerts × ~8 s sequential ≈ 4 min; with `Semaphore(5)` you get ~50 s. vLLM handles concurrent requests well by design — this is a pure throughput win.
```python
sem = asyncio.Semaphore(int(os.environ.get("SOC_CLAW_CONCURRENCY", "5")))
async def _run(a):
    async with sem:
        return await run_pipeline(a)
results = await asyncio.gather(*(_run(a) for a in alerts), return_exceptions=True)
```

### E2 🟡 Agents duplicate the route+client+messages+retry+log scaffold
[triage_agent.py:128-189](soc-claw/agents/triage_agent.py#L128), verifier_agent.py:100-164, response_agent.py:119-173 are 80% identical. Extract `async def call_llm(name, system, user, schema_hint, default_factory)` into utils.py. ~200 lines collapse to ~30, and the JSON-output story (Tier 2 outstanding item) becomes a one-place change.

### E3 🟡 Replace `_cache` globals with `functools.lru_cache`
Same correctness as B10 above; one decorator beats three hand-rolled caches.

### E4 🟡 No structured logging
`utils.py` builds log lines as f-strings (`f"{ts} | {route} | ..."`). Any aggregator (Loki, Datadog, Grafana) wants JSON. Switch to `logging.getLogger().info(msg, extra={"route": route, ...})` with a JSON formatter (`python-json-logger`). One-time change, works the rest of your career.

### E5 🟡 Tool I/O latency: enrichment runs sequentially
[triage_agent.py:53-92](soc-claw/agents/triage_agent.py#L53) calls IP rep + asset + MITRE in series. They're dict lookups today (microseconds — irrelevant), but if any becomes a real API the structure invites latency stacking. Wrap in `asyncio.gather` over `asyncio.to_thread` calls so the shape is right when the tools become async.

### E6 🟡 `docker-compose.yml` `app` and `benchmark` duplicate base config
Use a YAML anchor: `x-soc-claw: &soc-claw\n  build: .\n  env_file: [.env]\n  extra_hosts: ...` then `app: <<: *soc-claw, ports:..., command:...`. Cosmetic but clean.

### E7 🟢 `extract_json` raises a 200-char-truncated message
`utils.py:54` truncates `text[:200]` in the error. With long reasoning models (32k output) you lose the actual failure shape. Log the full text at DEBUG level, raise with the head and tail.

### E8 🟡 Backend code lives in `soc-claw/ui/`
Both `server.py` and `app.py` are HTTP server processes (backends), but they sit under [`soc-claw/ui/`](soc-claw/ui/), a folder named after the artifact they *serve* rather than what they *are*. The actual UI assets are `ui/templates/index.html` (HTML/JS for server.py) and the Gradio-rendered DOM (for app.py). A future contributor reading the tree thinks `ui/` is the frontend and looks elsewhere for the API server.

The hackathon-era grouping was "everything related to displaying the UI lives here" — pragmatic but imprecise. Industry-standard layouts put server code at the package level and frontend assets in a folder named after them:

```
soc-claw/
├── server.py            # FastAPI app — HTTP server at the package root
├── web/                 # frontend assets — what gets served to the browser
│   ├── templates/
│   │   └── index.html
│   └── static/          # css, js, images (none today)
├── agents/
├── tools/
├── pipeline.py
└── utils.py
```

The mechanical rename is small: move `soc-claw/ui/server.py` → `soc-claw/server.py`, rename `soc-claw/ui/` → `soc-claw/web/`, update `Jinja2Templates(directory=...)` in the new server.py, update `Dockerfile` `CMD ["python", "ui/server.py"]` → `["python", "server.py"]`, update [SETUP.md](SETUP.md) and any docs that reference the path. Naturally bundled with the S5 work (since deciding what to keep there is the same conversation).

---

## 📐 Production-readiness gaps

### P1 🔴 No automated tests
There are inline `if __name__ == "__main__"` smoke tests in each tool, but no `pytest` suite, no coverage, no CI. Per pivot memory, this is Tier 3 work and the right next move after fixing the bugs above. Minimum viable: `pytest` in `tests/` covering `route_request`, `extract_json`, `merge_verdict`, and the four tools. ~30 tests for ~150 lines of test code.

### P2 🟠 `requirements.txt` is unpinned
```
openai>=1.30
gradio>=4.0
fastapi
uvicorn[standard]
```
Production-track demands reproducibility. Either:
- `uv pip compile requirements.in -o requirements.txt` → fully pinned
- or move to `pyproject.toml` + `uv lock` (since you're already on uv)
Either way, `pip install -r requirements.txt` should yield identical builds across hosts.

### P3 🟠 No Pydantic schema validation on data files
`alerts.json`, `threat_intel.json`, `asset_inventory.json`, `mitre_techniques.json` are loaded by key access. A typo or missing field surfaces only at runtime, sometimes deep in the pipeline. Define Pydantic models, validate on load (`Alert.model_validate(...)`), error early. Sets up the same models for `guided_json` at agent decode time (Tier 2 outstanding item).

### P4 🟠 Observability is line-based logging
Per E4 — switch to JSON logs. Then add OpenTelemetry tracing around `chat.completions.create` and the three pipeline stages so you can see span trees per alert in Tempo/Jaeger/Datadog. This is the version of the LangSmith pitch I made earlier without the LangChain dependency.

### P5 🟡 README accuracy
[README.md:33-37](README.md#L33) claims "Triage 78% / Verified 88% / +10%" while [README.md:48](README.md#L48) shows a 30-alert run at "Triage 76.7% / Verified 63.3%" — verification *regressed* accuracy in that run. Production trust requires the marketing numbers and the benchmark numbers to either match or the discrepancy to be explained (e.g., "best of 5 runs" vs "single run"). Suggest: replace the 78/88 with the most recent harness output, label as "single 30-alert run, model X, date Y".

### P6 🟡 No model identity in benchmark output
`harness.py` writes `run_<timestamp>.csv` but doesn't record `SOC_CLAW_MODEL`, vLLM version, or git SHA. Future you (or a reviewer) cannot tell which model produced which numbers. Add a header row or a sidecar `.json` manifest.

### P7 🟢 No `__init__.py` exports + `sys.path.insert` everywhere
[triage_agent.py:6](soc-claw/agents/triage_agent.py#L6), [pipeline.py:32](soc-claw/pipeline.py#L32), [server.py:13](soc-claw/ui/server.py#L13), [harness.py:18](soc-claw/benchmark/harness.py#L18) all do `sys.path.insert(0, ...)`. This works; it's also a code smell. Convert to a proper package with `pyproject.toml` defining `soc_claw` so `from soc_claw.utils import ...` works from anywhere. Also fixes the Docker layout — one less surprise.

---

## Recommended next moves

Tasks in rough priority order. Effort is rough wall-clock: **S** ≈ <30 min, **M** ≈ 30-90 min, **L** ≈ half-day or more.

| # | Effort | What | Why |
|---|---|---|---|
| 1 | M | **UI consolidation** (S5 + E8): port `_severity` fix and streaming progress from `app.py` into `server.py`, then retire `app.py`; rename `soc-claw/ui/` → `soc-claw/web/` and move `server.py` to the package root. | Stops two-UI drift, fixes B1 in the production UI, gives the layout an honest name. |
| 2 | M | **Fix B2** (`pipeline.py:158-163` digit-domain mis-classification — replace heuristic with `ipaddress.ip_address`). | Real bug; cheap fix once you're already in `pipeline.py` for #1. |
| 3 | M | **Fix S2** (XSS in `index.html` — replace HTML interpolation of LLM fields with `textContent`). | Highest-impact security fix; small change. |
| 4 | S | **Bind compose to `127.0.0.1` and add a healthcheck** (S7 + `docker-compose.yml`). | Stops the unauthenticated UI being exposed on the Brev public IP. |
| 5 | M | **Pin dependencies** (P2 — `uv pip compile` or move to `pyproject.toml` + `uv lock`). | Reproducible builds; small change with long tail of benefit. |
| 6 | M | **Add a `tests/` directory with pytest** (P1 — start with `route_request`, `extract_json`, `merge_verdict`, the three pure tools, and `response_tools` with `freezegun` + `random.seed` + `capsys`). | Foundation for the next 6 months of changes. |
| 7 | M | **Concurrency on `harness.py` and `api_run_all`** (E1 — `asyncio.gather` with semaphore). | ~5× throughput; one-place change. |
| 8 | L | **Pydantic schemas + `guided_json`** (P3, Tier 2 outstanding item). | The honest version of the dropped NemoClaw `steering:` claim; replaces hand-rolled `extract_json` with decode-time enforcement. |
| 9 | L | **Refactor agents to a shared `call_llm` helper** (E2). | After (8), consolidating the duplicated scaffold becomes a ~30-line cleanup. |
| 10 | L | **Authn + CSRF** (S1 + S3). | Required before any production deploy with real EDR/firewall tools. |
| 11 | L | **JSON logging + OTEL tracing** (E4 + P4). | Production observability; integrates with the llm-d/k8s target. |

## Verification

This is a review document, not an implementation plan. "Verification" is: the user reads the findings, accepts / rejects / requests deeper explanation per item, and individual items convert to focused implementation work as the user chooses to take them on.
