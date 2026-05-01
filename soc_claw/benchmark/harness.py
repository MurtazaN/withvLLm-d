"""
SOC-Claw Benchmark Harness

Runs all 30 alerts through the pipeline and measures latency, accuracy,
verification effectiveness, and response plan quality.
"""

import asyncio
import csv
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from soc_claw.pipeline import run_pipeline, load_alerts


# Output directory is environment-driven so the same code works on host,
# in Docker (compose mounts /app/benchmark/results), and in production
# (k8s injects a path on a writable PVC).
#   - Host dev:    defaults to soc-claw/benchmark/results/
#   - Sandbox:     setup.sh writes BENCHMARK_OUTPUT_DIR=/sandbox/results
#   - Production:  orchestrator injects a path on a writable volume
RESULTS_DIR = Path(
    os.environ.get(
        "BENCHMARK_OUTPUT_DIR",
        str(Path(__file__).parent / "results"),
    )
)


def compute_percentile(values: list[float], p: float) -> float:
    """Compute the p-th percentile of a list of values."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(p / 100 * (len(sorted_vals) - 1))
    return sorted_vals[idx]


async def _process_alert(alert: dict, sem: asyncio.Semaphore) -> dict:
    """Run one alert end-to-end under a concurrency semaphore.

    Returns the row dict the harness aggregates. Errors are converted into
    an "ERROR" row rather than propagating, so a single bad alert doesn't
    abort the batch.
    """
    alert_id = alert["id"]
    gt = alert["ground_truth"]
    async with sem:
        try:
            result = await run_pipeline(alert)
            triage_sev = result["triage_result"].get("severity", "P3")
            verif = result["verification_result"]
            decision = verif.get("decision", "confirmed")
            verified_sev = (
                triage_sev if result["was_flagged"]
                else result["final_verdict"].get("verified_severity", triage_sev)
            )
            resp = result.get("response_plan", {}) or {}
            plan_steps = resp.get("response_plan", []) if isinstance(resp, dict) else []
            return {
                "alert_id": alert_id,
                "ground_truth_severity": gt["severity"],
                "triage_severity": triage_sev,
                "verified_severity": verified_sev,
                "verification_decision": decision,
                "triage_correct": triage_sev == gt["severity"],
                "verified_correct": verified_sev == gt["severity"],
                "triage_latency_ms": result["timing"]["triage_ms"],
                "verification_latency_ms": result["timing"]["verification_ms"],
                "response_latency_ms": result["timing"].get("response_ms", 0),
                "e2e_latency_ms": result["timing"]["total_ms"],
                "triage_confidence": result["triage_result"].get("confidence", 0),
                "verification_confidence": verif.get("confidence_in_verification", 0),
                "num_tool_calls": len(result["triage_result"].get("_tool_calls", [])),
                "num_response_steps": len(plan_steps),
                "num_approval_required": sum(1 for s in plan_steps if s.get("requires_approval")),
            }
        except Exception as e:
            print(f"ERROR on {alert_id}: {e}")
            return {
                "alert_id": alert_id,
                "ground_truth_severity": gt["severity"],
                "triage_severity": "ERROR",
                "verified_severity": "ERROR",
                "verification_decision": "error",
                "triage_correct": False,
                "verified_correct": False,
                "triage_latency_ms": 0,
                "verification_latency_ms": 0,
                "response_latency_ms": 0,
                "e2e_latency_ms": 0,
                "triage_confidence": 0,
                "verification_confidence": 0,
                "num_tool_calls": 0,
                "num_response_steps": 0,
                "num_approval_required": 0,
            }


def _decision_suffix(decision: str) -> str:
    if decision == "adjusted":
        return " (ADJUSTED)"
    if decision == "flagged":
        return " (FLAGGED)"
    return ""


def _format_row_line(completed: int, n_total: int, row: dict) -> str:
    if row["triage_severity"] == "ERROR":
        return f"[{completed:2d}/{n_total}] ✗ {row['alert_id']} ERROR"
    status = "✓" if row["verified_correct"] else "✗"
    return (
        f"[{completed:2d}/{n_total}] {status} {row['alert_id']} "
        f"GT={row['ground_truth_severity']} "
        f"Triage={row['triage_severity']} "
        f"Verified={row['verified_severity']}"
        f"{_decision_suffix(row['verification_decision'])} "
        f"[{row['e2e_latency_ms']}ms]"
    )


def _safe_mean(vals: list) -> int:
    return round(statistics.mean(vals)) if vals else 0


def _safe_pct(vals: list, p: float) -> int:
    return round(compute_percentile(vals, p)) if vals else 0


def _count_correct_adjustments(adjustments: list[dict]) -> int:
    """How many "adjusted" verdicts moved severity *closer* to ground truth.

    Defensive against malformed severity strings (e.g., LLM returning "P1A"
    or "Critical") — a row that fails to parse is skipped rather than
    crashing the whole benchmark.
    """
    correct = 0
    for r in adjustments:
        try:
            gt_n = int(r["ground_truth_severity"][1])
            orig_n = int(r["triage_severity"][1])
            new_n = int(r["verified_severity"][1])
        except (IndexError, ValueError):
            continue
        if abs(new_n - gt_n) < abs(orig_n - gt_n):
            correct += 1
    return correct


def _compute_metrics(results: list[dict], total_time: float) -> dict:
    """Aggregate per-alert rows into the benchmark metrics dict."""
    valid = [r for r in results if r["triage_severity"] != "ERROR"]
    n = len(valid)

    triage_lats = [r["triage_latency_ms"] for r in valid]
    verif_lats = [r["verification_latency_ms"] for r in valid]
    resp_lats = [r["response_latency_ms"] for r in valid]
    e2e_lats = [r["e2e_latency_ms"] for r in valid]

    triage_correct_count = sum(1 for r in valid if r["triage_correct"])
    verified_correct_count = sum(1 for r in valid if r["verified_correct"])
    triage_acc = triage_correct_count / n * 100 if n else 0
    verified_acc = verified_correct_count / n * 100 if n else 0

    fp_alerts = [r for r in valid if r["ground_truth_severity"] == "P4"]
    tp_alerts = [r for r in valid if r["ground_truth_severity"] == "P1"]
    fp_correct = sum(1 for r in fp_alerts if r["verified_severity"] == "P4")
    fn_count = sum(1 for r in tp_alerts if r["verified_severity"] in ("P3", "P4"))
    fp_rate = fp_correct / len(fp_alerts) * 100 if fp_alerts else 0
    fn_rate = fn_count / len(tp_alerts) * 100 if tp_alerts else 0

    decisions = [r["verification_decision"] for r in valid]
    confirm_rate = decisions.count("confirmed") / n * 100 if n else 0
    adjust_rate = decisions.count("adjusted") / n * 100 if n else 0
    flag_rate = decisions.count("flagged") / n * 100 if n else 0

    adjustments = [r for r in valid if r["verification_decision"] == "adjusted"]
    adj_correct = _count_correct_adjustments(adjustments)
    adj_correct_rate = adj_correct / len(adjustments) * 100 if adjustments else 0

    step_counts = [r["num_response_steps"] for r in valid]
    approval_counts = [r["num_approval_required"] for r in valid]
    avg_steps = statistics.mean(step_counts) if step_counts else 0
    total_steps = sum(step_counts)
    total_approvals = sum(approval_counts)
    approval_rate = total_approvals / total_steps * 100 if total_steps else 0
    throughput = len(valid) / total_time * 60 if total_time else 0

    return {
        "total_alerts": n,
        "total_time_s": round(total_time, 1),
        "throughput_alerts_per_min": round(throughput, 1),
        "latency": {
            "triage": {"avg": _safe_mean(triage_lats), "p50": _safe_pct(triage_lats, 50), "p95": _safe_pct(triage_lats, 95)},
            "verification": {"avg": _safe_mean(verif_lats), "p50": _safe_pct(verif_lats, 50), "p95": _safe_pct(verif_lats, 95)},
            "response": {"avg": _safe_mean(resp_lats), "p50": _safe_pct(resp_lats, 50), "p95": _safe_pct(resp_lats, 95)},
            "e2e": {"avg": _safe_mean(e2e_lats), "p50": _safe_pct(e2e_lats, 50), "p95": _safe_pct(e2e_lats, 95)},
        },
        "accuracy": {
            "triage_raw": round(triage_acc, 1),
            "triage_verified": round(verified_acc, 1),
            "improvement": round(verified_acc - triage_acc, 1),
            "fp_detection_rate": round(fp_rate, 1),
            "fn_rate": round(fn_rate, 1),
            "_triage_correct_count": triage_correct_count,
            "_verified_correct_count": verified_correct_count,
            "_fp_correct": fp_correct,
            "_fp_total": len(fp_alerts),
            "_fn_count": fn_count,
            "_fn_total": len(tp_alerts),
        },
        "verification": {
            "confirm_rate": round(confirm_rate, 1),
            "adjust_rate": round(adjust_rate, 1),
            "flag_rate": round(flag_rate, 1),
            "adjustment_correct_rate": round(adj_correct_rate, 1),
            "_adj_correct": adj_correct,
            "_adj_total": len(adjustments),
        },
        "response_plan": {
            "avg_steps": round(avg_steps, 1),
            "approval_required_rate": round(approval_rate, 1),
        },
    }


def _print_summary(metrics: dict, total_time: float) -> None:
    n = metrics["total_alerts"]
    acc = metrics["accuracy"]
    ver = metrics["verification"]
    rp = metrics["response_plan"]

    print(f"\n{'='*70}")
    print("BENCHMARK RESULTS")
    print(f"{'='*70}")
    print(f"\nAlerts processed: {n} | Total time: {total_time:.1f}s | "
          f"Throughput: {metrics['throughput_alerts_per_min']} alerts/min")

    print("\n--- LATENCY (ms) ---")
    print(f"{'Stage':<15} {'Avg':>8} {'P50':>8} {'P95':>8}")
    for stage in ["triage", "verification", "response", "e2e"]:
        m = metrics["latency"][stage]
        print(f"{stage:<15} {m['avg']:>8} {m['p50']:>8} {m['p95']:>8}")

    print("\n--- ACCURACY ---")
    print(f"Triage (raw):      {acc['triage_raw']:5.1f}%  ({acc['_triage_correct_count']}/{n})")
    print(f"Triage (verified): {acc['triage_verified']:5.1f}%  ({acc['_verified_correct_count']}/{n})")
    print(f"Improvement:       {acc['improvement']:+5.1f}%")
    print(f"FP detection rate: {acc['fp_detection_rate']:5.1f}%  ({acc['_fp_correct']}/{acc['_fp_total']})")
    print(f"FN rate:           {acc['fn_rate']:5.1f}%  ({acc['_fn_count']}/{acc['_fn_total']})")

    print("\n--- VERIFICATION ---")
    print(f"Confirmed: {ver['confirm_rate']:5.1f}%  "
          f"Adjusted: {ver['adjust_rate']:5.1f}%  "
          f"Flagged: {ver['flag_rate']:5.1f}%")
    print(f"Adjustment correct: {ver['adjustment_correct_rate']:5.1f}%  "
          f"({ver['_adj_correct']}/{ver['_adj_total']})")

    print("\n--- RESPONSE PLAN ---")
    print(f"Avg steps/plan: {rp['avg_steps']:.1f}  "
          f"Approval required: {rp['approval_required_rate']:.1f}%")

    print(f"\n{'='*70}")
    print(f"Before verification: {acc['triage_raw']:.1f}% → "
          f"After verification: {acc['triage_verified']:.1f}% "
          f"({acc['improvement']:+.1f}%)")
    print(f"{'='*70}\n")


def _save_csv(results: list[dict]) -> Path | None:
    if not results:
        return None
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = RESULTS_DIR / f"run_{timestamp}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    return csv_path


async def run_benchmark(max_alerts: int = 30) -> dict:
    """Run the full benchmark across all alerts.

    Alerts are processed concurrently up to `SOC_CLAW_CONCURRENCY` (default
    5) — vLLM batches concurrent requests well so this is a near-linear
    speedup. Output ordering is by completion time, not alert ID; the final
    CSV is sorted by alert_id at the end so it stays stable across runs.
    """
    alerts = load_alerts()[:max_alerts]
    n_total = len(alerts)
    concurrency = max(1, int(os.environ.get("SOC_CLAW_CONCURRENCY", "5")))

    print(f"\n{'='*70}")
    print(f"SOC-Claw Benchmark — {n_total} alerts (concurrency={concurrency})")
    print(f"{'='*70}\n")

    sem = asyncio.Semaphore(concurrency)
    total_start = time.perf_counter()

    tasks = [asyncio.create_task(_process_alert(a, sem)) for a in alerts]
    results: list[dict] = []
    completed = 0
    for fut in asyncio.as_completed(tasks):
        row = await fut
        completed += 1
        results.append(row)
        print(_format_row_line(completed, n_total, row))

    total_time = time.perf_counter() - total_start
    results.sort(key=lambda r: r["alert_id"])

    metrics = _compute_metrics(results, total_time)
    _print_summary(metrics, total_time)

    csv_path = _save_csv(results)
    if csv_path:
        print(f"Results saved to: {csv_path}")

    return metrics


if __name__ == "__main__":
    from soc_claw.logging_config import setup_logging
    from soc_claw.telemetry import setup_tracing

    # Default WARNING keeps the summary table readable; override with
    # SOC_CLAW_LOG_LEVEL=INFO for CI / production where the JSON stream
    # is the point of running the harness.
    os.environ.setdefault("SOC_CLAW_LOG_LEVEL", "WARNING")
    setup_logging()
    setup_tracing()

    max_alerts = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    metrics = asyncio.run(run_benchmark(max_alerts))
