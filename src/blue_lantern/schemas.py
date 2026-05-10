"""Blue Lantern Pydantic schemas.

"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


_SYNTHETIC_SEVERITY_TO_PRIO = {
    "critical": "P1",
    "high": "P2",
    "medium": "P3",
    "low": "P4",
    "info": "P4",
}

class GroundTruth(BaseModel):
    """Dev-only ground truth embedded in each alert."""

    model_config = ConfigDict(extra="allow")
    severity: str
    is_malicious: bool
    expected_actions: list[str] = []


class Alert(BaseModel):
    """A raw security alert from the SIEM / data file.

    Accepts both the canonical shape (``id`` / ``hostname`` /
    ``rule_name``) and the Blue Lantern synthetic-dataset shape
    (``event_id`` / ``user`` / ``description`` / ``raw_log`` / ...).
    A ``mode='before'`` validator runs ahead of field validation to
    project synthetic-only fields onto the canonical ones; canonical
    rows pass through untouched. ``extra='allow'`` keeps every original
    field available so downstream agents (triage, verifier) still see
    ``advanced_metadata``, ``behavioral_analytics``, etc.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(validation_alias=AliasChoices("id", "event_id"))
    timestamp: str
    hostname: str
    rule_name: str
    source_ip: str | None = None
    dest_ip: str | None = None
    payload: str = ""
    severity: str | None = None
    ground_truth: GroundTruth | None = None

    @model_validator(mode="before")
    @classmethod
    def _project_synthetic_fields(cls, data: Any) -> Any:
        """Fill in canonical fields from synthetic-shape inputs.

        Each rule fires only when its target field is missing/empty,
        so canonical alerts (already carrying ``hostname``,
        ``rule_name``, etc.) are untouched. The mapping rules:

        * ``hostname``     ← ``user`` || short ``advanced_metadata.device_hash``
        * ``rule_name``    ← ``description`` || ``event_type:action``
        * ``payload``      ← ``raw_log``
        * ``severity``     low/medium/high/critical → P4/P3/P2/P1
        * ``ground_truth`` synthesized from the projected severity
        """
        if not isinstance(data, dict):
            return data
        out = dict(data)

        # hostname proxy — synthetic dataset is user-centric and has
        # no host field; username or a short device hash both serve as
        # the "what was affected" key downstream.
        if not out.get("hostname"):
            user = out.get("user")
            device_hash = (out.get("advanced_metadata") or {}).get("device_hash", "")
            derived = user or (device_hash[:12] if device_hash else "")
            if derived:
                out["hostname"] = derived

        # rule_name — prefer the human-readable description, else
        # fall back to event_type:action as a coarse rule label.
        if not out.get("rule_name"):
            description = out.get("description")
            event_type = out.get("event_type", "")
            action = out.get("action", "")
            derived = (
                description
                or (f"{event_type}:{action}" if event_type and action else event_type)
            )
            if derived:
                out["rule_name"] = derived

        # payload — prefer the CEF/syslog raw_log if present.
        if not out.get("payload"):
            raw_log = out.get("raw_log")
            if raw_log:
                out["payload"] = raw_log

        # severity — coerce string severities to P-tier in place.
        sev_raw = str(out.get("severity", "")).strip().lower()
        if sev_raw in _SYNTHETIC_SEVERITY_TO_PRIO:
            out["severity"] = _SYNTHETIC_SEVERITY_TO_PRIO[sev_raw]

        # ground_truth — synthesize from the severity so the dashboard's
        # accuracy stats have a comparison target. High/critical → malicious.
        if not out.get("ground_truth") and out.get("severity"):
            prio = out["severity"]
            out["ground_truth"] = {
                "severity": prio,
                "is_malicious": prio in ("P1", "P2"),
                "expected_actions": [],
            }

        return out


class ThreatIntelEntry(BaseModel):
    """One row from ``threat_intel.json``."""

    model_config = ConfigDict(extra="allow")

    indicator: str
    type: str
    threat_score: int
    tags: list[str] = []
    campaigns: list[str] = []
    first_seen: str | None = None
    last_seen: str | None = None


class Asset(BaseModel):
    """One row from ``asset_inventory.json``."""

    model_config = ConfigDict(extra="allow")

    hostname: str
    criticality: str
    business_function: str
    owner: str | None = None
    os: str | None = None
    last_patch: str | None = None
    network_zone: str | None = None


class MitreTechnique(BaseModel):
    """One row from ``mitre_techniques.json``."""

    model_config = ConfigDict(extra="allow")

    technique_id: str
    name: str
    tactic: str
    keywords: list[str]
    description: str = ""


# ── Half B: Output schemas (strict, for guided_json) ──────────────────


class IOCFound(BaseModel):
    """Single IOC identified during triage.

    The IOC type list mirrors what real SIEM enrichment surfaces:
    network identifiers, file hashes, identities, and HTTP-layer
    fingerprints. ``other`` is the catch-all for anything that doesn't
    fit, so the LLM never has to invent a value outside the literal.
    """

    indicator: str
    type: Literal[
        "ip",
        "domain",
        "url",
        "hash",
        "file",
        "user",
        "user_agent",
        "process",
        "other",
    ]
    # Real threat-intel feeds emit floats (0-100 risk scores with a
    # fractional part) and the LLM mirrors that when it picks up
    # numbers like ``risk_score: 61.04`` from the alert payload.
    # Accept floats so we don't reject valid output over a rounding nit.
    threat_score: float = 0


class TriageVerdict(BaseModel):
    """Expected output from the Triage Agent."""

    severity: Literal["P1", "P2", "P3", "P4"]
    confidence: int = Field(ge=0, le=100)
    reasoning: str
    mitre_techniques: list[str] = []
    iocs_found: list[IOCFound] = []
    # ``unknown`` covers the production case where an alert's hostname
    # isn't in the asset inventory / CMDB, which happens routinely on
    # newly-onboarded or shadow-IT assets.
    asset_criticality: Literal["critical", "high", "medium", "low", "unknown"]
    recommended_urgency: Literal["immediate", "urgent", "standard", "monitor"]


class VerificationDecision(BaseModel):
    """Expected output from the Verifier Agent."""

    decision: Literal["confirmed", "adjusted", "flagged"]
    original_severity: Literal["P1", "P2", "P3", "P4"]
    verified_severity: Literal["P1", "P2", "P3", "P4"]
    confidence_in_verification: int = Field(ge=0, le=100)
    reasoning: str
    issues_found: list[str] = []
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    recommendation: str = ""


class ResponseStep(BaseModel):
    """A single step in a response plan."""

    step: int
    action: str
    action_type: Literal[
        "isolate_host",
        "block_ioc",
        "create_ticket",
        "escalate",
        "collect_forensics",
        "add_to_watchlist",
        "notify_owner",
        "tune_rule",
    ]
    target: str
    reasoning: str
    urgency: Literal["immediate", "within_30min", "within_24hrs", "when_convenient"]
    requires_approval: bool


class ResponsePlan(BaseModel):
    """Expected output from the Response Agent."""

    alert_id: str
    severity_acted_on: Literal["P1", "P2", "P3", "P4"]
    was_adjusted: bool
    response_plan: list[ResponseStep]
    incident_summary: str
    analyst_notes: str = ""
    estimated_mttr_impact: str = ""
