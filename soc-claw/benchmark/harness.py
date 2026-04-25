"""
SOC-Claw Benchmark Harness

Runs all 30 alerts through the pipeline and measures latency, accuracy,
verification effectiveness, and response plan quality.
"""

import asyncio
import csv
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import run_pipeline, load_alerts


RESULTS_DIR = Path(__file__).parent / "results"


def compute_percentile(values: list[float], p: float) -> float:
    """Compute the p-th percentile of a list of values."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(p / 100 * (len(sorted_vals) - 1))
    return sorted_vals[idx]


async def run_benchmark(max_alerts: int = 30) -> dict:
    """Run the full benchmark across all alerts."""
    alerts = load_alerts()[:max_alerts]
    results = []

    print(f"\n{'='*70}")
    print(f"SOC-Claw Benchmark — {len(alerts)} alerts")
    print(f"{'='*70}\n")

    total_start = time.perf_counter()

    for i, alert in enumerate(alerts):
        alert_id = alert["id"]
        gt = alert["ground_truth"]
        print(f"[{i+1:2d}/{len(alerts)}] Processing {alert_id}...", end=" ", flush=True)

        try:
            result = await run_pipeline(alert)

            triage_sev = result["triage_result"].get("severity", "P3")
            verif = result["verification_result"]
            decision = verif.get("decision", "confirmed")

            # For flagged alerts in benchmark mode, auto-use triage severity
            if result["was_flagged"]:
                verified_sev = triage_sev
            else:
                verified_sev = result["final_verdict"].get("verified_severity", triage_sev)

            triage_correct = triage_sev == gt["severity"]
            verified_correct = verified_sev == gt["severity"]

            resp = result.get("response_plan", {})
            plan_steps = resp.get("response_plan", []) if resp else []
            num_steps = len(plan_steps)
            num_approval = sum(1 for s in plan_steps if s.get("requires_approval"))

            row = {
                "alert_id": alert_id,
                "ground_truth_severity": gt["severity"],
                "triage_severity": triage_sev,
                "verified_severity": verified_sev,
                "verification_decision": decision,
                "triage_correct": triage_correct,
                "verified_correct": verified_correct,
                "triage_latency_ms": result["timing"]["triage_ms"],
                "verification_latency_ms": result["timing"]["verification_ms"],
                "response_latency_ms": result["timing"].get("response_ms", 0),
                "e2e_latency_ms": result["timing"]["total_ms"],
                "triage_confidence": result["triage_result"].get("confidence", 0),
                "verification_confidence": verif.get("confidence_in_verification", 0),
                "num_tool_calls": len(result["triage_result"].get("_tool_calls", [])),
                "num_response_steps": num_steps,
                "num_approval_required": num_approval,
            }
            results.append(row)

            status = "✓" if verified_correct else "✗"
            adj = " (ADJUSTED)" if decision == "adjusted" else " (FLAGGED)" if decision == "flagged" else ""
            print(f"{status} GT={gt['severity']} Triage={triage_sev} Verified={verified_sev}{adj} [{result['timing']['total_ms']}ms]")

        except Exception as e:
            print(f"ERROR: {e}")
            results.append({
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
            })

    total_time = time.perf_counter() - total_start

    # ── Compute Metrics ──
    valid = [r for r in results if r["triage_severity"] != "ERROR"]
    n = len(valid)

    # Latency
    triage_lats = [r["triage_latency_ms"] for r in valid]
    verif_lats = [r["verification_latency_ms"] for r in valid]
    resp_lats = [r["response_latency_ms"] for r in valid]
    e2e_lats = [r["e2e_latency_ms"] for r in valid]

    # Accuracy
    triage_correct_count = sum(1 for r in valid if r["triage_correct"])
    verified_correct_count = sum(1 for r in valid if r["verified_correct"])
    triage_accuracy = triage_correct_count / n * 100 if n else 0
    verified_accuracy = verified_correct_count / n * 100 if n else 0
    accuracy_improvement = verified_accuracy - triage_accuracy

    # FP/FN rates (based on alert categories)
    fp_alerts = [r for r in valid if r["ground_truth_severity"] == "P4"]
    tp_alerts = [r for r in valid if r["ground_truth_severity"] == "P1"]
    fp_correct = sum(1 for r in fp_alerts if r["verified_severity"] == "P4")
    fn_count = sum(1 for r in tp_alerts if r["verified_severity"] in ("P3", "P4"))
    fp_rate = fp_correct / len(fp_alerts) * 100 if fp_alerts else 0
    fn_rate = fn_count / len(tp_alerts) * 100 if tp_alerts else 0

    # Verification metrics
    decisions = [r["verification_decision"] for r in valid]
    confirm_rate = decisions.count("confirmed") / n * 100 if n else 0
    adjust_rate = decisions.count("adjusted") / n * 100 if n else 0
    flag_rate = decisions.count("flagged") / n * 100 if n else 0

    adjustments = [r for r in valid if r["verification_decision"] == "adjusted"]
    adj_correct = 0
    for r in adjustments:
        gt = r["ground_truth_severity"]
        orig_dist = abs(int(r["triage_severity"][1]) - int(gt[1]))
        new_dist = abs(int(r["verified_severity"][1]) - int(gt[1]))
        if new_dist < orig_dist:
            adj_correct += 1
    adj_correct_rate = adj_correct / len(adjustments) * 100 if adjustments else 0

    # Response plan metrics
    step_counts = [r["num_response_steps"] for r in valid]
    approval_counts = [r["num_approval_required"] for r in valid]
    avg_steps = statistics.mean(step_counts) if step_counts else 0
    total_steps = sum(step_counts)
    total_approvals = sum(approval_counts)
    approval_rate = total_approvals / total_steps * 100 if total_steps else 0

    throughput = len(valid) / total_time * 60 if total_time else 0

    metrics = {
        "total_alerts": n,
        "total_time_s": round(total_time, 1),
        "throughput_alerts_per_min": round(throughput, 1),
        "latency": {
            "triage": {"avg": round(statistics.mean(triage_lats)), "p50": round(compute_percentile(triage_lats, 50)), "p95": round(compute_percentile(triage_lats, 95))},
            "verification": {"avg": round(statistics.mean(verif_lats)), "p50": round(compute_percentile(verif_lats, 50)), "p95": round(compute_percentile(verif_lats, 95))},
            "response": {"avg": round(statistics.mean(resp_lats)), "p50": round(compute_percentile(resp_lats, 50)), "p95": round(compute_percentile(resp_lats, 95))},
            "e2e": {"avg": round(statistics.mean(e2e_lats)), "p50": round(compute_percentile(e2e_lats, 50)), "p95": round(compute_percentile(e2e_lats, 95))},
        },
        "accuracy": {
            "triage_raw": round(triage_accuracy, 1),
            "triage_verified": round(verified_accuracy, 1),
            "improvement": round(accuracy_improvement, 1),
            "fp_detection_rate": round(fp_rate, 1),
            "fn_rate": round(fn_rate, 1),
        },
        "verification": {
            "confirm_rate": round(confirm_rate, 1),
            "adjust_rate": round(adjust_rate, 1),
            "flag_rate": round(flag_rate, 1),
            "adjustment_correct_rate": round(adj_correct_rate, 1),
        },
        "response_plan": {
            "avg_steps": round(avg_steps, 1),
            "approval_required_rate": round(approval_rate, 1),
        },
    }

    # ── Print Summary ──
    print(f"\n{'='*70}")
    print("BENCHMARK RESULTS")
    print(f"{'='*70}")
    print(f"\nAlerts processed: {n} | Total time: {total_time:.1f}s | Throughput: {throughput:.1f} alerts/min")

    print(f"\n--- LATENCY (ms) ---")
    print(f"{'Stage':<15} {'Avg':>8} {'P50':>8} {'P95':>8}")
    for stage in ["triage", "verification", "response", "e2e"]:
        m = metrics["latency"][stage]
        print(f"{stage:<15} {m['avg']:>8} {m['p50']:>8} {m['p95']:>8}")

    print(f"\n--- ACCURACY ---")
    print(f"Triage (raw):      {triage_accuracy:5.1f}%  ({triage_correct_count}/{n})")
    print(f"Triage (verified): {verified_accuracy:5.1f}%  ({verified_correct_count}/{n})")
    print(f"Improvement:       {accuracy_improvement:+5.1f}%")
    print(f"FP detection rate: {fp_rate:5.1f}%  ({fp_correct}/{len(fp_alerts)})")
    print(f"FN rate:           {fn_rate:5.1f}%  ({fn_count}/{len(tp_alerts)})")

    print(f"\n--- VERIFICATION ---")
    print(f"Confirmed: {confirm_rate:5.1f}%  Adjusted: {adjust_rate:5.1f}%  Flagged: {flag_rate:5.1f}%")
    print(f"Adjustment correct: {adj_correct_rate:5.1f}%  ({adj_correct}/{len(adjustments)})")

    print(f"\n--- RESPONSE PLAN ---")
    print(f"Avg steps/plan: {avg_steps:.1f}  Approval required: {approval_rate:.1f}%")

    print(f"\n{'='*70}")
    print(f"Before verification: {triage_accuracy:.1f}% → After verification: {verified_accuracy:.1f}% ({accuracy_improvement:+.1f}%)")
    print(f"{'='*70}\n")

    # ── Save CSV ──
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = RESULTS_DIR / f"run_{timestamp}.csv"

    if results:
        fieldnames = list(results[0].keys())
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"Results saved to: {csv_path}")

    return metrics


if __name__ == "__main__":
    max_alerts = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    metrics = asyncio.run(run_benchmark(max_alerts))
