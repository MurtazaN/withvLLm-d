# Plan 03B — Fix: "Process All" dashboard flow

**Parent:** [plan-03-cutover-siem.md](plan-03-cutover-siem.md), *Implementation status* → "Sidebar: legacy 'Run All 30' / `/api/run-all` benchmark view still wired" + *Deferred to v2* → "UI cleanup of legacy benchmark view"
**Status:** Draft — proposal, not yet implemented.

## Goal

Make the dashboard's **"Process All"** button actually process every alert in GCS and stream per-alert progress to the analyst. Today it navigates to a benchmark view whose "Start Benchmark" button hits a deleted route and silently 404s.

## Symptoms (from a fresh `docker compose up`)

- Click "PROCESS ALL" on the dashboard → page switches to the benchmark view, nothing else happens.
- Click "Start Benchmark" on the benchmark view → progress UI stays at 0/0 forever; browser DevTools shows a 404 on `/api/run-all`.
- No alerts processed, no SSE events, no error toast.

## Root causes

Three problems chained:

1. **Dashboard button is a navigation, not an action.** [`src/blue_lantern/frontend/templates/index.html:81`](../src/blue_lantern/frontend/templates/index.html#L81) — `onclick="showView('benchmark')"`. It only swaps which view is visible.
2. **Benchmark view points at a deleted SSE route.** [`runAllAlerts()` at index.html:645](../src/blue_lantern/frontend/templates/index.html#L645) opens `EventSource('/api/run-all')`. That handler was removed from `api.py` during the GCS cutover; only `POST /api/process-batch` and `GET /api/process-all` exist now.
3. **Per-row stream payload may reference a stale field.** `_process_alert_for_stream` in [`src/blue_lantern/backend/routers/api.py:190`](../src/blue_lantern/backend/routers/api.py#L190) reads `result["final_verdict"]`. The pipeline currently emits `triage_result` / `verification_result` / `response_plan` — `final_verdict` is the older name. With the synthetic `Alert` schema projection now populating `ground_truth.severity`, the *first* part of the function (reading ground truth) works, but the *verified-severity* path may KeyError once it actually runs.

## Scope

- **In:** Re-point the SSE URL, route the dashboard button to a working flow, verify the per-row aggregation, decommission the legacy "Run All 30" naming.
- **Out:** True "all" pagination (lifting the `download_batch(bucket, max_results=1000)` cap is already deferred to v2 in the parent plan); rebuilding the benchmark UI; any benchmark-mode features beyond live progress.

## Pinned decisions

| Decision | Choice | Rationale |
| :--- | :--- | :--- |
| Live progress UI | Reuse the existing benchmark view markup | Already polished, already tested; rebuilding inline on the dashboard is more code than the symptom warrants. |
| Dashboard button behaviour | Trigger `runAllAlerts()` *and* auto-switch to the benchmark view | One button, one path, no duplicate progress UI. |
| SSE endpoint | `GET /api/process-all` | Plan-03's chosen name; we're aligning the frontend, not adding a new route. |
| Legacy naming | Rename "Run All 30" → "Process All" in the sidebar + benchmark heading | The `30` was tied to the old static dataset and is meaningless now that the source is GCS. |
| 1000-alert cap | Leave as-is for v1 | Pagination is already a v2 carry-over in the parent plan. |

## Steps (implementation detail)

1. **Re-point the SSE URL.** In [`index.html:670`](../src/blue_lantern/frontend/templates/index.html#L670), change `EventSource('/api/run-all')` to `EventSource('/api/process-all')`. The event names (`start` / `result` / `summary`) match between the old and new endpoints, so the JS handlers stay untouched.

2. **Wire the dashboard "Process All" button to the SSE flow.**
   - Change the `onclick` at [`index.html:81`](../src/blue_lantern/frontend/templates/index.html#L81) from `showView('benchmark')` to a new helper `processAllAlerts()`.
   - `processAllAlerts()` calls `showView('benchmark')` *then* `runAllAlerts()`. The view switch is the live progress display.

3. **Verify the per-row payload structure against the current pipeline.**
   - Hit `POST /api/process-batch` once with `batch_size=1` and inspect the JSON response. Confirm whether the verified severity lives under `result["final_verdict"]["verified_severity"]` or `result["verification_result"]["verified_severity"]`.
   - If `final_verdict` is gone, fix [`_process_alert_for_stream` in api.py:190](../src/blue_lantern/backend/routers/api.py#L190) to read from the actual key. Same applies to `result.get("was_flagged")`.

4. **Decommission the legacy naming.**
   - Rename the sidebar nav entry at [`index.html:31-32`](../src/blue_lantern/frontend/templates/index.html#L31-L32) from "Run All 30" to "Process All".
   - Rename the benchmark view heading at [`index.html:277`](../src/blue_lantern/frontend/templates/index.html#L277) to "Process All — Live Progress".
   - Rename the button label at [`index.html:280`](../src/blue_lantern/frontend/templates/index.html#L280) from "Start Benchmark" to "Start" (the dashboard button is the primary entry point now; the benchmark-view button is a manual fallback).

5. **Smoke test.**
   - `docker compose restart app`.
   - Dashboard → "Process All" → progress bar advances, rows fill in, summary renders at the end.
   - Open the benchmark view directly via the sidebar → "Start" still works as a manual fallback.

## Acceptance criteria

- Clicking dashboard **"Process All"** opens the benchmark view AND immediately starts streaming per-alert results.
- The progress bar reaches 100% with no `404 /api/run-all` requests in the network tab.
- Each row in the live table shows ground-truth, triage severity, verified severity, match (✓/✗), decision, and latency. No `ERR` rows from `KeyError` on missing pipeline fields.
- The summary card renders at the end with totals + accuracy %.
- The sidebar entry no longer says "Run All 30".

## Risks

- **Pipeline output schema drift.** Step 3 assumes the v1 pipeline still has the same top-level result keys it had pre-cutover. If `final_verdict` was renamed *and* other handlers (approve, override) also reference it, fixing one place may not be enough. Mitigation: grep for `final_verdict` across `src/blue_lantern/` before editing.
- **1000-alert cap surfacing late.** Once the flow works, an analyst running it against a large bucket will see *exactly* 1000 alerts and assume it's "all". Mitigation: surface the cap in the SSE `summary` event so the UI can show "1000 of N processed" when truncated.
- **CSRF on SSE.** `EventSource` is GET-only, so the CSRFMiddleware doesn't gate it — should be fine, but worth confirming the auth middleware lets it through with the session cookie attached. Mitigation: smoke-test step 5 covers this.

## Open questions

- Do we want to keep a separate "benchmark view" at all, or fold its progress UI into the dashboard so "Process All" never navigates? The current plan keeps it for v1 (less code churn); v2 could collapse them.
- The `_RunAllAggregator` sorts results by `alert_id` for the final summary. Does that key exist on the synthetic dataset post-projection? It should (`event_id` → `id`), but worth a console glance during smoke testing.

## Out of scope (carry-overs)

- True pagination of `/api/process-all` past 1000 alerts → tracked in parent plan's *Deferred to v2*.
- Process-time filters (date range, severity, source) → not in v1 surface area.
- Persisting "Process All" results into a job record (Redis) for re-display after a refresh → batch flow already does this; live SSE flow doesn't need it for v1.
