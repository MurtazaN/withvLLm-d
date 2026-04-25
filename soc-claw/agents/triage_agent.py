import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import (
    extract_json,
    get_client,
    route_request,
    log_routing_decision,
    log_tool_call,
    log_inference,
    MODEL_NAME,
)
from tools.ip_reputation import ip_reputation
from tools.mitre_lookup import mitre_lookup
from tools.asset_lookup import asset_lookup

SYSTEM_PROMPT = """You are a SOC Tier 2 security analyst performing alert triage. When given a raw security alert, you MUST follow this exact workflow:

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

Be precise. Be consistent. When in doubt between two severity levels, choose the higher one — missed true positives are more costly than false escalations."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ip_reputation",
            "description": "Look up IP address against threat intelligence database. Returns threat_score (0-100), tags, campaigns, and verdict (malicious/suspicious/clean/unknown).",
            "parameters": {
                "type": "object",
                "properties": {
                    "ip": {
                        "type": "string",
                        "description": "IPv4 address to look up",
                    }
                },
                "required": ["ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mitre_lookup",
            "description": "Map observed behavior description to MITRE ATT&CK techniques. Returns top 1-3 matching techniques with technique_id, name, tactic, and match_score.",
            "parameters": {
                "type": "object",
                "properties": {
                    "behavior": {
                        "type": "string",
                        "description": "Natural language description of observed behavior, e.g. 'powershell encoded command downloading payload from external IP'",
                    }
                },
                "required": ["behavior"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "asset_lookup",
            "description": "Retrieve asset information from CMDB/inventory. Returns hostname, criticality (critical/high/medium/low), business_function, owner, OS, and network_zone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hostname": {
                        "type": "string",
                        "description": "Host identifier string",
                    }
                },
                "required": ["hostname"],
            },
        },
    },
]

# Map function names to actual functions
TOOL_FUNCTIONS = {
    "ip_reputation": ip_reputation,
    "mitre_lookup": mitre_lookup,
    "asset_lookup": asset_lookup,
}


def _execute_tool(name: str, arguments: dict) -> str:
    """Execute a tool call and return the result as a JSON string."""
    start = time.perf_counter()
    func = TOOL_FUNCTIONS.get(name)
    if not func:
        result = {"error": f"Unknown tool: {name}"}
    else:
        try:
            result = func(**arguments)
        except Exception as e:
            result = {"error": str(e)}
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    log_tool_call(name, arguments, result if isinstance(result, dict) else {}, elapsed_ms)
    return json.dumps(result)


async def run_triage(alert: dict, steering_context: str = None) -> dict:
    """Run the Triage Agent on a raw alert.

    Returns the triage verdict dict with enrichment data.
    """
    # Build user message
    alert_json = json.dumps(alert, indent=2)
    if steering_context:
        user_content = f"ANALYST CONTEXT: {steering_context}\n\nAlert: {alert_json}"
    else:
        user_content = f"Alert: {alert_json}"

    # Route the request
    route, reason = route_request(user_content)
    log_routing_decision("triage", route, reason, user_content)
    client = get_client(route)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    tool_calls_log = []
    inference_start = time.perf_counter()

    # Tool-calling loop
    max_iterations = 10
    for _ in range(max_iterations):
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        message = response.choices[0].message

        if message.tool_calls:
            # Append assistant message with tool calls
            messages.append(message)

            # Execute each tool call
            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                result_str = _execute_tool(func_name, func_args)
                tool_calls_log.append({
                    "tool": func_name,
                    "input": func_args,
                    "output": json.loads(result_str),
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                })
        else:
            # Final response — no more tool calls
            break

    inference_ms = int((time.perf_counter() - inference_start) * 1000)
    log_inference("triage", route, inference_ms)

    # Extract JSON verdict from final response
    content = message.content or ""
    try:
        verdict = extract_json(content)
    except ValueError:
        # Retry once with a reminder
        messages.append({
            "role": "user",
            "content": "Please output your triage verdict as valid JSON with the required fields: severity, confidence, reasoning, mitre_techniques, iocs_found, asset_criticality, recommended_urgency.",
        })
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
        )
        content = response.choices[0].message.content or ""
        try:
            verdict = extract_json(content)
        except ValueError:
            verdict = {
                "severity": "P3",
                "confidence": 30,
                "reasoning": "Failed to parse LLM response. Defaulting to P3 for manual review.",
                "mitre_techniques": [],
                "iocs_found": [],
                "asset_criticality": "medium",
                "recommended_urgency": "standard",
            }

    # Attach enrichment metadata
    verdict["_tool_calls"] = tool_calls_log
    verdict["_inference_ms"] = inference_ms
    verdict["_route"] = route
    verdict["_raw_response"] = content

    return verdict
