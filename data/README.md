# Data for the agents

## advanced_siem_dataset.json (Advanced SIEM)
- The advanced_siem_dataset is a synthetic dataset of 100,000 security event records.
- **Purpose**: It simulates logs from Security Information and Event Management (SIEM) systems, capturing diverse event types such as firewall activities, intrusion detection system (IDS) alerts, authentication attempts, endpoint activities, network traffic, cloud operations, IoT device events, and AI system interactions.
- **Structure**: The dataset includes advanced metadata, MITRE ATT&CK techniques, threat actor associations, and unconventional indicators of compromise (IOCs), making it suitable for tasks like anomaly detection, threat classification, predictive analytics, and user and entity behavior analytics (UEBA).
- Source: https://huggingface.co/datasets/darkknight25/Advanced_SIEM_Dataset

## incident_response_playbook_dataset.jsonl (RAG Knowledge Base)
- This is a collection of structured Incident Response (IR) playbooks, formatted as JSON Lines (one JSON object per line).
- **Purpose**: It serves as the knowledge base for the Response Agent. The agent uses Retrieval-Augmented Generation (RAG) to find and apply the most relevant playbook steps for a verified incident.
- **Structure**: Each line contains a complete playbook, including: `playbook_id`, `title`, `description`, `severity`, `tactic`, and a list of `steps` (each with `order`, `action`, and `rationale`).
- Source: https://www.kaggle.com/datasets/cyberprince/incident-response-playbook-dataset?select=incident_response_playbook_dataset.jsonl

## threat_intel_data.json (Real Threat Intelligence)
- This file contains real-world Indicators of Compromise (IOCs) harvested from Abuse.ch ThreatFox (recent exports).
- **Purpose**: It provides the Triage Agent with up-to-date, real-world malicious IPs and associated threat data to validate against live network traffic.
- **Structure**: A list of objects, each containing `indicator` (IP address), `threat_score`, `tags` (including malware families like Cobalt Strike, AsyncRAT), `first_seen`, and `last_seen` timestamps.
- Source: Fetched live from https://threatfox.abuse.ch/export/json/recent/

## asset_inventory_data.json (Synthetic Data for CMDB)
- This dataset simulates a Configuration Management Database (CMDB) with 10,000 assets.
- **Purpose**: It provides the Triage Agent with a comprehensive inventory of organizational assets, including their types, criticality, and relationships, which is essential for accurate incident triage and prioritization.
- **Structure**: Each asset record includes `asset_id`, `name`, `type`, `criticality`, `owner`, and a list of `related_assets` (by asset_id).
- Source: Generated synthetically to mimic real-world asset inventories (ServiceNow), ensuring diversity in asset types and criticality levels.
