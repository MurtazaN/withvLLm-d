"""NemoClaw Guardrails Enforcement.

Loads soc_claw/config/nemoclaw_policy.yaml and enforces it at three
pipeline stages:

  check_input(prompt, source)
      Called before any LLM sees the alert payload.
      Blocks prompt-injection patterns; truncates oversized payloads.

  check_triage_output(triage_result, alert) -> dict
      Called after the Triage Agent returns its verdict.
      Annotates the result with _guardrail_flags (does not raise);
      the pipeline propagates flags to the Verifier and UI.

  check_action(action, severity, confidence, analyst)
      Called inside execute_approved_action before any tool fires.
      Raises GuardrailViolation if the action is not permitted.

All violations are logged as structured JSON events regardless of whether
they raise; the audit.log_* switches in the policy control verbosity.
"""

import logging
import re
import time
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger("soc-claw.guardrails")

_POLICY_PATH = Path(__file__).parent / "config" / "nemoclaw_policy.yaml"


# ── Policy loader ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_policy() -> dict:
    with open(_POLICY_PATH) as f:
        return yaml.safe_load(f)


# ── Exception ─────────────────────────────────────────────────────────────

class GuardrailViolation(Exception):
    """Raised when a hard guardrail blocks pipeline progression."""

    def __init__(self, rule: str, detail: str):
        self.rule = rule
        self.detail = detail
        super().__init__(f"[{rule}] {detail}")


# ── Sliding-window state ──────────────────────────────────────────────────

# Blast-radius counters: {action_type: deque of epoch timestamps}
_action_timestamps: dict[str, deque] = defaultdict(deque)

# Escalation-bias window: deque of recent severity strings (P1-P4)
_recent_severities: deque = deque()


def _record_action(action_type: str) -> None:
    _action_timestamps[action_type].append(time.time())


def _count_recent_actions(action_type: str, window_secs: int = 3600) -> int:
    dq = _action_timestamps[action_type]
    cutoff = time.time() - window_secs
    while dq and dq[0] < cutoff:
        dq.popleft()
    return len(dq)


def _coerce_int(value, default: int = 0) -> int:
    """Best-effort int coercion for values that arrive over JSON.

    Returns default for None, non-numeric strings, or any TypeError. Used
    on confidence fields before they reach the condition evaluator, which
    compares numerically and would raise on str/None.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _record_severity(severity: str, window_size: int) -> None:
    _recent_severities.append(severity)
    while len(_recent_severities) > window_size:
        _recent_severities.popleft()


def _p1_rate() -> float:
    if not _recent_severities:
        return 0.0
    return sum(1 for s in _recent_severities if s == "P1") / len(_recent_severities)


# ── Condition evaluator ───────────────────────────────────────────────────

def _eval_condition(condition: str, ctx: dict) -> bool:
    """Evaluate a compound condition string against a context dict.

    Supports clauses joined by ' and ':
      severity == P1
      severity in [P1, P2]
      asset_criticality == critical
      asset_criticality in [critical, high]
      confidence < 40
      confidence >= 70
    """
    for clause in re.split(r"\s+and\s+", condition.strip()):
        if not _eval_clause(clause.strip(), ctx):
            return False
    return True


def _eval_clause(clause: str, ctx: dict) -> bool:
    # severity == P1
    m = re.fullmatch(r"severity\s*==\s*(P[1-4])", clause)
    if m:
        return ctx.get("severity") == m.group(1)

    # severity in [P1, P2]
    m = re.fullmatch(r"severity\s+in\s+\[([^\]]+)\]", clause)
    if m:
        vals = {s.strip() for s in m.group(1).split(",")}
        return ctx.get("severity") in vals

    # asset_criticality == critical
    m = re.fullmatch(r"asset_criticality\s*==\s*(\w+)", clause)
    if m:
        return ctx.get("asset_criticality") == m.group(1)

    # asset_criticality in [critical, high]
    m = re.fullmatch(r"asset_criticality\s+in\s+\[([^\]]+)\]", clause)
    if m:
        vals = {s.strip() for s in m.group(1).split(",")}
        return ctx.get("asset_criticality") in vals

    # confidence <op> N
    m = re.fullmatch(r"confidence\s*(<|>=|<=|>|==)\s*(\d+)", clause)
    if m:
        op, threshold = m.group(1), int(m.group(2))
        conf = ctx.get("confidence", 0)
        return {
            "<": conf < threshold,
            ">": conf > threshold,
            "<=": conf <= threshold,
            ">=": conf >= threshold,
            "==": conf == threshold,
        }[op]

    logger.debug("guardrails: unparseable condition clause: %r", clause)
    return False


# ── Stage 1: Input Rails ──────────────────────────────────────────────────

def check_input(prompt: str, source: str = "unknown") -> str:
    """Screen the prompt for injection patterns; truncate if oversized.

    Returns the (possibly truncated) prompt to use.
    Raises GuardrailViolation if injection is detected and action == block.
    """
    policy = _load_policy()
    rail = policy.get("input_rails", {})
    audit = policy.get("audit", {})

    inj = rail.get("prompt_injection", {})
    if inj.get("enabled"):
        lower = prompt.lower()
        for pattern in inj.get("patterns", []):
            if pattern.lower() in lower:
                if audit.get("log_all_violations"):
                    logger.warning(
                        "guardrail_violation",
                        extra={
                            "event": "guardrail_violation",
                            "rule": "input.prompt_injection",
                            "pattern": pattern,
                            "source": source,
                        },
                    )
                if inj.get("action") == "block":
                    raise GuardrailViolation(
                        "input.prompt_injection",
                        f"Prompt injection pattern detected in {source!r}: {pattern!r}",
                    )

    max_len = rail.get("max_payload_length")
    if max_len and len(prompt) > max_len:
        logger.info(
            "guardrail_truncate",
            extra={
                "event": "guardrail_truncate",
                "rule": "input.max_payload_length",
                "original_length": len(prompt),
                "truncated_to": max_len,
                "source": source,
            },
        )
        prompt = prompt[:max_len]

    return prompt


# ── Stage 2: Triage Output Rails ──────────────────────────────────────────

def check_triage_output(triage_result: dict, alert: dict) -> dict:
    """Validate the Triage Agent's verdict against triage_rails.

    Never raises — instead annotates triage_result with '_guardrail_flags',
    a list of human-readable strings the pipeline surfaces to the analyst.
    Also updates the escalation-bias sliding window.
    """
    policy = _load_policy()
    rail = policy.get("triage_rails", {})
    audit = policy.get("audit", {})

    severity = triage_result.get("severity", "P3")
    confidence = _coerce_int(triage_result.get("confidence"), default=0)
    asset_criticality = triage_result.get("asset_criticality", "medium")
    iocs = triage_result.get("iocs_found", [])
    alert_id = alert.get("id", "unknown")

    flags: list[str] = []

    # Update escalation-bias window
    bias_cfg = rail.get("escalation_bias", {})
    window_size = bias_cfg.get("window_size", 10)
    _record_severity(severity, window_size)

    # Confidence floor
    min_conf = rail.get("min_confidence", 0)
    if confidence < min_conf:
        flags.append(
            f"confidence {confidence} is below policy minimum {min_conf} — treat as uncertain"
        )

    # P1 prerequisite check
    if severity == "P1":
        p1_req = rail.get("p1_requires", [])

        if "confirmed_malicious_ioc" in p1_req:
            has_malicious = any(i.get("threat_score", 0) >= 80 for i in iocs)
            if not has_malicious:
                flags.append(
                    "P1 verdict lacks a confirmed malicious IOC (threat_score >= 80) "
                    "— escalation bias likely"
                )

        if "critical_or_high_asset" in p1_req:
            if asset_criticality not in ("critical", "high"):
                flags.append(
                    f"P1 verdict on {asset_criticality!r} asset — "
                    "evidence does not meet P1 threshold; consider P2"
                )

    # Escalation-bias check
    if bias_cfg.get("enabled") and severity == "P1":
        threshold = bias_cfg.get("p1_rate_threshold", 0.6)
        rate = _p1_rate()
        if rate > threshold:
            flags.append(
                f"escalation bias: {rate:.0%} of recent {window_size} alerts "
                f"were P1 (threshold {threshold:.0%}) — verify this verdict independently"
            )

    # Force-review conditions
    ctx = {"severity": severity, "asset_criticality": asset_criticality, "confidence": confidence}
    for rule in rail.get("force_review_conditions", []):
        condition = rule.get("condition", "")
        if condition and _eval_condition(condition, ctx):
            flags.append(f"force_review: {rule.get('reason', condition)}")

    if flags and audit.get("log_guardrail_flags"):
        logger.info(
            "guardrail_triage_flags",
            extra={
                "event": "guardrail_triage_flags",
                "alert_id": alert_id,
                "severity": severity,
                "confidence": confidence,
                "flags": flags,
            },
        )

    if flags:
        triage_result["_guardrail_flags"] = flags

    return triage_result


# ── Stage 3: Response Action Rails ────────────────────────────────────────

def check_action(
    action: dict,
    severity: str,
    confidence: int = 100,
    analyst: str = "unknown",
) -> None:
    """Validate an action against the response_rails before execution.

    Raises GuardrailViolation if:
      - the action_type is not allowed for the given severity
      - the blast-radius ceiling for this action type is exceeded
      - the target is a protected asset and extra conditions are not met
    """
    policy = _load_policy()
    resp_rails = policy.get("response_rails", {})
    audit = policy.get("audit", {})

    action_type = action.get("action_type", "")
    target = action.get("target", "")
    confidence = _coerce_int(confidence, default=0)
    ctx = {"severity": severity, "confidence": confidence}

    matrix = resp_rails.get("action_matrix", {})
    rule = matrix.get(action_type)

    if rule is None:
        # Unknown action type — fail closed by default (safer for a security
        # tool). Set response_rails.unknown_action_policy: allow in the YAML
        # to opt into the legacy log-and-permit behaviour.
        unknown_policy = resp_rails.get("unknown_action_policy", "deny")
        _log_violation(audit, "response.unknown_action", {
            "action_type": action_type,
            "policy": unknown_policy,
            "analyst": analyst,
        })
        if unknown_policy == "deny":
            raise GuardrailViolation(
                "response.unknown_action",
                f"Action {action_type!r} is not in the action_matrix. "
                f"Add it to nemoclaw_policy.yaml or set unknown_action_policy: allow.",
            )
        return

    # ── Severity check ────────────────────────────────────────────────────
    allowed = rule.get("allowed_severities", [])
    if severity not in allowed:
        _log_violation(audit, f"response.{action_type}.severity", {
            "action_type": action_type,
            "severity": severity,
            "allowed_severities": allowed,
            "analyst": analyst,
        })
        raise GuardrailViolation(
            f"response.{action_type}.severity",
            f"Action {action_type!r} is not permitted for severity {severity}. "
            f"Allowed severities: {allowed}",
        )

    # ── Approval marker check ─────────────────────────────────────────────
    # When the policy says requires_approval, the caller must record who
    # approved the action by setting action["_approved_by"] (the server
    # populates this from the authenticated session). Catches CLI/test
    # paths that bypass the analyst-click flow.
    if rule.get("requires_approval"):
        approver = action.get("_approved_by")
        if not approver:
            _log_violation(audit, f"response.{action_type}.missing_approval", {
                "action_type": action_type,
                "analyst": analyst,
            })
            raise GuardrailViolation(
                f"response.{action_type}.missing_approval",
                f"Action {action_type!r} requires explicit analyst approval "
                f"(action['_approved_by']) but none was recorded.",
            )

    # ── Blast-radius check ────────────────────────────────────────────────
    blast = rule.get("blast_radius") or {}
    max_per_hour = blast.get("max_per_hour")
    if max_per_hour is not None:
        recent = _count_recent_actions(action_type, window_secs=3600)
        if recent >= max_per_hour:
            _log_violation(audit, f"response.{action_type}.blast_radius", {
                "action_type": action_type,
                "count_last_hour": recent,
                "max_per_hour": max_per_hour,
                "analyst": analyst,
            })
            raise GuardrailViolation(
                f"response.{action_type}.blast_radius",
                f"Action {action_type!r} rate limit reached: "
                f"{recent}/{max_per_hour} executions in the last hour.",
            )

    # ── Protected-asset check (host isolation only) ───────────────────────
    # block_ioc targets are IPs / domains / hashes, not hostnames, so the
    # protected_assets list (and require_for_isolation key) only applies
    # to isolate_host. Hostname comparison is case-insensitive.
    if action_type == "isolate_host":
        target_norm = str(target).casefold()
        for asset in resp_rails.get("protected_assets", []):
            if str(asset.get("hostname", "")).casefold() == target_norm:
                for cond in asset.get("require_for_isolation", []):
                    if not _eval_condition(cond, ctx):
                        _log_violation(audit, "response.protected_asset", {
                            "action_type": action_type,
                            "target": target,
                            "failed_condition": cond,
                            "severity": severity,
                            "confidence": confidence,
                            "analyst": analyst,
                        })
                        raise GuardrailViolation(
                            "response.protected_asset",
                            f"Target {target!r} is a protected asset. "
                            f"Condition not met: {cond!r} "
                            f"(severity={severity}, confidence={confidence}). "
                            f"Note: {asset.get('note', '')}",
                        )

    # ── Record action for blast-radius tracking ───────────────────────────
    _record_action(action_type)


# ── Internal helpers ──────────────────────────────────────────────────────

def _log_violation(audit: dict, rule: str, extra: dict) -> None:
    if audit.get("log_all_violations") or audit.get("log_blocked_actions"):
        logger.warning(
            "guardrail_violation",
            extra={"event": "guardrail_violation", "rule": rule, **extra},
        )
