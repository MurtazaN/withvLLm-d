# SOC-Claw — Clean Code & SOLID Principles Review

**Date:** 2026-05-01  
**Scope:** All Python source in `soc_claw/` (~2,400 LoC across 18 files), plus tests, Dockerfile, and config.  
**Complements:** [docs/CODE_REVIEW.md](CODE_REVIEW.md) (bugs, security, production-readiness).  
This document focuses exclusively on **code quality**, **design principles**, and **maintainability**.

---

## Severity Legend

| Icon | Meaning |
|------|---------|
| 🟠 | High — structural issue that impedes maintainability or extensibility |
| 🟡 | Medium — quality concern; fix during normal cleanup |
| 🟢 | Low — style / cosmetic / future-proofing |

---

## Table of Contents

1. [SOLID Analysis](#-solid-analysis)
2. [Clean Code Findings](#-clean-code-findings)
3. [What's Already Clean](#-whats-already-clean)
4. [Prioritized Action Table](#-prioritized-action-table)

---

## 📐 SOLID Analysis

### S — Single Responsibility Principle

**Most modules pass.** Each agent, tool, schema, and infra module owns one concern:

| Module | Responsibility | Verdict |
|--------|---------------|---------|
| `schemas.py` | Data contracts (input + output) | ✅ |
| `pipeline.py` | Orchestrate agents, merge verdicts, dispatch actions | ✅ |
| `logging_config.py` | Configure structured logging | ✅ |
| `telemetry.py` | Configure OTEL tracing | ✅ |
| `auth.py` | Sessions, users, password hashing | ✅ |
| `security.py` | Build SecurityConfig from env | ✅ |
| `triage_agent.py` | Run enrichment + LLM triage | ✅ |
| `verifier_agent.py` | Verify triage verdict via LLM | ✅ |
| `response_agent.py` | Generate response plan via LLM | ✅ |
| `ip_reputation.py` | IP threat-intel lookup | ✅ |
| `mitre_lookup.py` | MITRE ATT&CK keyword matching | ✅ |
| `asset_lookup.py` | Asset inventory lookup | ✅ |
| `response_tools.py` | Simulate EDR/firewall/ITSM actions | ✅ |
| `harness.py` | Benchmark orchestration + metrics | ✅ |

**Two violations:**

#### S1 🟠 `utils.py` is a God Module (347 lines, 6 responsibilities)

`utils.py` bundles six unrelated concerns:

| Lines | Concern |
|-------|---------|
| 1–17 | Environment bootstrap (`load_dotenv`, `CONFIG_DIR`) |
| 23–58 | JSON extraction from LLM output |
| 61–92 | Privacy routing (config loading + decision logic) |
| 95–129 | OpenAI client factory + model name |
| 132–212 | Five structured-logging helpers |
| 215–345 | Guided-JSON builder, parse helper, `call_llm()` scaffold |

**Why it matters:** Every import of `utils` loads the dotenv, YAML, and OpenAI client machinery even if the caller only needs `extract_json`. Tests must mock through a single monolith. New contributors can't find functionality by file name.

**Suggested split:**

```
soc_claw/
├── llm/
│   ├── __init__.py
│   ├── client.py         # get_client(), MODEL_NAME, guided_json_kwargs()
│   ├── caller.py         # call_llm(), _try_parse()
│   └── json_extract.py   # extract_json()
├── routing.py            # load_privacy_routes(), route_request()
├── audit.py              # log_routing_decision(), log_tool_call(), etc.
```

#### S2 🟡 `server.py` bundles pages, API, SSE, auth routes, and middleware

At 415 lines this is borderline. Acceptable at current scale, but as endpoints grow, split with `APIRouter`:

```python
# backend/routes/api.py    — JSON endpoints
# backend/routes/pages.py  — HTML pages
# backend/routes/auth.py   — login/logout
# backend/middleware.py     — CSP, auth, Guard registration
```

---

### O — Open/Closed Principle

#### O1 🟠 Adding a new enrichment tool requires editing 4 files

Current steps to add e.g. `whois_lookup`:
1. Create `tools/whois_lookup.py`
2. Add import in `tools/__init__.py`
3. Import + call in `triage_agent.py:_run_enrichment()`
4. Update the triage system prompt to describe it

Every new tool modifies existing code in 3 places — classic OCP violation.

**Fix — Tool Protocol + Registry:**

```python
# tools/base.py
from typing import Protocol

class EnrichmentTool(Protocol):
    name: str
    description: str  # injected into LLM prompt automatically
    def extract_input(self, alert: dict) -> dict: ...
    def run(self, **kwargs) -> dict: ...

# tools/registry.py
_REGISTRY: list[EnrichmentTool] = []

def register(tool: EnrichmentTool) -> None:
    _REGISTRY.append(tool)

def get_all() -> list[EnrichmentTool]:
    return list(_REGISTRY)
```

Then `_run_enrichment()` iterates the registry, and the system prompt is built dynamically from each tool's `description`. New tool = new file + one `register()` call. Zero edits to existing code.

#### O2 🟡 `execute_approved_action()` uses if/elif dispatch

`pipeline.py:208–228` maps `action_type` → function via a 5-branch if/elif chain. Each new action type requires editing this function.

**Fix — dispatch dict:**

```python
_DISPATCH: dict[str, Callable] = {
    "isolate_host": lambda a, **kw: response_tools.isolate_host(a["target"]),
    "block_ioc": lambda a, **kw: response_tools.block_ioc(
        a["target"], _classify_indicator(a["target"])
    ),
    "create_ticket": _handle_ticket,
    "escalate": _handle_escalate,
}

def execute_approved_action(action, alert=None, *, analyst="unknown"):
    handler = _DISPATCH.get(action.get("action_type"))
    if handler:
        return handler(action, alert=alert, analyst=analyst)
    return {"status": "logged", "note": f"Requires manual execution."}
```

#### O3 🟡 Pipeline stages are hardcoded

`run_pipeline()` calls `run_triage → run_verification → run_response` in fixed order. Adding a stage (e.g., a deduplication agent) means editing `run_pipeline()`. A stage list pattern would make it extensible:

```python
_STAGES = [run_triage, run_verification, run_response]
```

This is low priority — the 3-stage pipeline is unlikely to change often.

---

### L — Liskov Substitution Principle

✅ **No violations.** The codebase doesn't use class inheritance. Pydantic models use composition correctly (`Alert` contains `GroundTruth`, `ResponsePlan` contains `list[ResponseStep]`).

---

### I — Interface Segregation Principle

#### I1 🟡 `call_llm()` returns a raw 4-tuple

```python
async def call_llm(...) -> tuple[dict, int, str, str]:
```

Callers must remember positional order: `(result, inference_ms, route, raw_content)`. A typed return would be self-documenting and allow adding fields without breaking callers:

```python
from typing import NamedTuple

class LLMResult(NamedTuple):
    result: dict
    inference_ms: int
    route: str
    raw_content: str
```

#### I2 🟢 `_run_enrichment()` returns a 5-tuple

`triage_agent.py:50` returns `(ip_result, asset_result, mitre_results, tool_calls_log, source_ip_result)`. Same positional-order problem. A `dataclass` or `NamedTuple` would help, especially since `source_ip_result` is `None` in the common case.

---

### D — Dependency Inversion Principle

#### D1 🟡 Tools depend on concrete filesystem paths

All three lookup tools hardcode:
```python
DATA_DIR = Path(__file__).parent.parent / "data"
```

This couples tools to the project layout. Tests can't inject fixture data without monkeypatching the module-level constant.

**Fix:** Accept `data_dir` as a parameter with a default:

```python
def _load_threat_intel(data_dir: Path = None) -> tuple:
    data_dir = data_dir or Path(__file__).parent.parent / "data"
    ...
```

#### D2 🟡 `call_llm()` creates its own client

`utils.py:294` calls `get_client(route)` internally. The caller can't inject a mock client for testing. Accept an optional `client` parameter:

```python
async def call_llm(..., client: AsyncOpenAI | None = None) -> LLMResult:
    if client is None:
        client = get_client(route)
```

#### D3 🟢 `load_alerts()` is tightly coupled to a file path

`pipeline.py:240` hardcodes `Path(__file__).parent / "data" / "alerts.json"`. Same pattern as D1. Low priority since alerts are static test data, but a `data_dir` parameter would improve testability.

---

## 🔍 Clean Code Findings

### Naming

| # | Sev | Location | Issue | Suggestion |
|---|-----|----------|-------|------------|
| N1 | 🟢 | `response_tools.py:8` | `_now()` is too terse | `_utc_timestamp()` |
| N2 | 🟢 | `server.py:217` | `_sse()` is cryptic out of context | `_format_sse_event()` |
| N3 | 🟢 | `utils.py:228` | `_try_parse()` — "try" is redundant for a helper | `_parse_llm_output()` |
| N4 | 🟢 | `harness.py:104` | `_decision_suffix()` — fine, but could be a dict lookup | `_DECISION_LABELS = {"adjusted": " (ADJUSTED)", ...}` |

### DRY Violations (Code Duplication)

#### DRY1 🟡 Steering-context injection repeated 3 times

Each agent builds the user prompt with identical logic:

```python
if steering_context:
    user_content = f"ANALYST CONTEXT: {steering_context}\n\n..."
else:
    user_content = f"ALERT:\n{alert_json}\n\n..."
```

Appears in `triage_agent.py:142–152`, `verifier_agent.py:88–95`, `response_agent.py:107–114`.

**Fix:** Extract into `call_llm()` or a shared helper:

```python
def build_prompt(sections: dict[str, str], steering: str | None = None) -> str:
    parts = []
    if steering:
        parts.append(f"ANALYST CONTEXT: {steering}")
    for label, content in sections.items():
        parts.append(f"{label}:\n{content}")
    return "\n\n".join(parts)
```

#### DRY2 🟡 Metadata stripping repeated 4 times

```python
{k: v for k, v in d.items() if not k.startswith("_")}
```

Found in `pipeline.py:54`, `verifier_agent.py:85`, `response_agent.py:104`, `server.py:208–210`.

**Fix:** `def strip_internal(d: dict) -> dict` — one-liner, but centralizes the convention.

#### DRY3 🟡 Data-loading boilerplate repeated in all 3 tools

Each tool has a nearly identical `@lru_cache` loader:

```python
@lru_cache(maxsize=1)
def _load_X() -> tuple:
    from soc_claw.schemas import XModel
    with open(DATA_DIR / "x.json") as f:
        raw = json.load(f)
    validated = []
    for i, item in enumerate(raw):
        try:
            validated.append(XModel.model_validate(item).model_dump())
        except Exception as exc:
            _logger.warning("Skipping index %d: %s", i, exc)
    return tuple(validated)
```

**Fix:** Generic loader:

```python
def load_validated_json(path: Path, schema, logger: logging.Logger) -> tuple:
    with open(path) as f:
        raw = json.load(f)
    out = []
    for i, item in enumerate(raw):
        try:
            out.append(schema.model_validate(item).model_dump())
        except Exception as e:
            logger.warning("Skipping index %d: %s", i, e)
    return tuple(out)
```

### Function Complexity

| # | Sev | Function | Lines | Issue |
|---|-----|----------|-------|-------|
| F2 | 🟡 | `_compute_metrics()` | 75 | Sequential but long. Extract `_latency_metrics()`, `_accuracy_metrics()`, `_verification_metrics()`. |
| F3 | 🟢 | `call_llm()` | 63 | Acceptable — well-commented stages. Could extract retry logic into a `_retry_llm_call()` helper. |
| F4 | 🟢 | `_print_summary()` | 40 | Pure formatting — fine for its purpose. |

### Convention Inconsistencies

#### CV1 🟡 Private-field convention in dicts is fragile

Agent results attach metadata as `_inference_ms`, `_route`, `_raw_response`, `_tool_calls`, then strip with `k.startswith("_")`. If an external schema ever produces a field starting with `_`, it gets silently dropped.

**Fix:** Use a nested key: `result["_meta"] = {"inference_ms": ..., "route": ..., "raw_response": ...}`. Stripping becomes `result.pop("_meta", None)`.

#### CV2 🟢 Logger naming inconsistency

Most modules use `logging.getLogger("soc-claw.X")` (hyphenated), which is fine. But `response_tools.py:5` uses `"soc-claw.response_tools"` (underscore in the last segment) while tools like `ip_reputation.py:7` use `"soc-claw.tools.ip_reputation"` (dotted hierarchy). Pick one convention.

#### CV3 🟢 `if __name__ == "__main__"` test blocks in tools

`ip_reputation.py:62–81`, `mitre_lookup.py:51–71`, `asset_lookup.py:48–66`, `response_tools.py:97–117` all have inline smoke tests. These are useful for quick manual checks but should be migrated to `tests/` for CI. They're not harmful — just redundant once pytest coverage exists.

### Dead / Aspirational Code

#### DC1 🟡 `privacy_routes.yaml` cloud_inference block is never evaluated

The `cloud_inference.route_when` entries use `description:` keys, not `pattern:` keys. `route_request()` only iterates `local_inference.route_when`. The cloud block is aspirational config that looks active.

**Fix:** Either add `pattern:` keys and implement cloud-side matching, or move the block to a YAML comment to signal it's not live.

#### DC2 🟢 `benchmark/__init__.py` is empty

Not harmful, but it could export `run_benchmark` for convenience: `from .harness import run_benchmark`.

---

## ✅ What's Already Clean

Worth calling out patterns that are **done right** — these should be preserved:

1. **`call_llm()` consolidation** — All three agents share one LLM call path with routing, retry, guided_json, and default-factory. Textbook DRY. The earlier state had 80% identical scaffolds in each agent.

2. **Pydantic schema design** — Input schemas are `extra="allow"` (forward-compatible with new SIEM fields); output schemas are strict `Literal` types for `guided_json`. The two halves serve different purposes and are correctly separated.

3. **`merge_verdict()` state machine** — Clean 4-branch handler (confirmed/adjusted/flagged/unknown) with each branch documenting its invariants. No silent fall-through.

4. **`_classify_indicator()` using `ipaddress.ip_address()`** — stdlib validation instead of string heuristics. Prior version (see CODE_REVIEW.md B2) had a real bug here; current implementation is correct.

5. **`_RunAllAggregator` class** — Encapsulates accumulation logic (counts, accuracy, errors) away from the SSE stream loop. `server.py`'s stream generator is a thin sequence of yields.

6. **Env-driven configuration everywhere** — `get_client()`, `build_security_config()`, `RESULTS_DIR`, `MODEL_NAME`, `SESSION_MAX_AGE` all read from `os.environ` with sensible defaults. Same image works in dev, Docker, and k8s.

7. **Graceful LLM degradation** — Each agent's `default_factory` produces a deterministic fallback derived from enrichment data (not another LLM call). Parse failure → retry once → deterministic default. No infinite loops.

8. **Tool modules are pure functions** — `ip_reputation()`, `asset_lookup()`, `mitre_lookup()` are deterministic after cache load. Easy to unit test. `response_tools` is the exception (side effects by design).

9. **Observability stack** — `logging_config.py` (JSON + trace context) and `telemetry.py` (OTEL + auto-instrumentation) are clean, focused modules. `TraceContextFilter` correctly injects `trace_id`/`span_id` into log records.

10. **Auth module** — `auth.py` cleanly separates session management from user store. Bcrypt hashing, `secrets.token_urlsafe`, expiry enforcement, env-driven user provisioning.

---

## 📋 Prioritized Action Table

Effort: **S** < 30 min, **M** 30–90 min, **L** half-day+.

| # | Sev | Effort | Ref | Action |
|---|-----|--------|-----|--------|
| 1 | 🟠 | L | S1 | Split `utils.py` into `llm/`, `routing.py`, `audit.py` |
| 2 | 🟠 | M | O1 | Tool protocol + registry for OCP-compliant enrichment |
| 3 | 🟡 | S | DRY1 | Extract `build_prompt()` helper for steering context |
| 4 | 🟡 | S | DRY2 | Extract `strip_internal()` for metadata removal |
| 5 | 🟡 | S | DRY3 | Extract `load_validated_json()` generic data loader |
| 6 | 🟡 | S | I1 | Replace `call_llm` 4-tuple with `NamedTuple` |
| 7 | 🟡 | S | O2 | Replace if/elif in `execute_approved_action` with dispatch dict |
| 8 | 🟡 | S | CV1 | Consolidate `_`-prefixed metadata into `_meta` nested dict |
| 9 | 🟡 | M | D1, D2 | Add `data_dir` / `client` params for dependency injection |
| 10 | 🟡 | S | F1 | Reformat `_default_plan()` dict literals to multi-line |
| 11 | 🟡 | M | F2 | Extract metric sub-functions from `_compute_metrics()` |
| 12 | 🟡 | S | DC1 | Resolve `privacy_routes.yaml` cloud block (implement or comment) |
| 13 | 🟡 | M | S2 | Split `server.py` into `APIRouter` modules |
| 14 | 🟢 | S | N1–N4 | Rename terse helpers (`_now`, `_sse`, `_try_parse`) |
| 15 | 🟢 | S | CV2 | Standardize logger naming convention |
| 16 | 🟢 | S | CV3 | Migrate `__main__` smoke tests to `tests/` |

---

## SOLID Scorecard

| Principle | Grade | Summary |
|-----------|-------|---------|
| **S** — Single Responsibility | **B+** | Clean everywhere except `utils.py` (God Module) |
| **O** — Open/Closed | **B−** | Tools and action dispatch require editing existing code |
| **L** — Liskov Substitution | **A** | Not applicable — no inheritance hierarchies |
| **I** — Interface Segregation | **A−** | Minor: tuple returns instead of typed objects |
| **D** — Dependency Inversion | **B** | LLM client abstracted; data paths and client creation hardcoded |

**Overall: B+** — Strong foundation. The highest-impact improvements are splitting `utils.py` (S1) and introducing a tool registry (O1). Both are mechanical refactors with zero behavior change.
