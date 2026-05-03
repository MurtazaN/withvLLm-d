# Self Generated Sample data for the agents

## alerts.json (SIEM/XDR)
- This mimics/represents the constant stream of raw incidents that a SIEM (like Splunk or Sentinel) is generating based on network logs and endpoint telemetry. In SoC-Claw app, these are the starting point of the pipeline.

## threat_intel.json (TIP)
- This mimics/represents a Threat Intelligence Platform. It stores Indicators of Compromise (IoCs) like IPs and file hashes, along with reputation scores. The Triage Agent queries this to see if an IP in the alert is known to be malicious.

## asset_inventory.json (CMDB)
- This is a Configuration Management Database. It holds the "blueprint" of the company. It maps hostnames to physical locations, owner departments, and criticality. The Triage Agent queries this to determine the impact of the attack (e.g., an attack on a high-criticality asset raises the alert severity).

## mitre_techniques.json (Knowledge Base)
- This is a structured Knowledge Base like the MITRE ATT&CK framework. It lists out Tactics, Techniques, and Procedures (TTPs) like "OS Credential Dumping" along with keywords. The Triage Agent uses this to map the raw alert description to known attacker behaviors.