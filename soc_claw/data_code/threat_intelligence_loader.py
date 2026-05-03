import urllib.request
import json
import os

def update_threat_intel():
    print("Fetching recent Threat Intel from Abuse.ch ThreatFox...")
    
    # Direct link to the JSON export of recent threats
    url = "https://threatfox.abuse.ch/export/json/recent/"
    
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            
        new_intel = []
        
        # ThreatFox puts all the data inside a dictionary under the key "1" or similar, 
        # but the JSON export structure is usually {"id": {"ioc": "...", "malware": "..."}}
        for key, entry in data.items():
            # If the entry is a list, some exports wrap the data differently
            if isinstance(entry, list):
                for item in entry:
                    process_item(item, new_intel)
            elif isinstance(entry, dict):
                 process_item(entry, new_intel)
                 
        # Save it to your root data directory
        target_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'threat_intel_data.json')
        with open(target_path, 'w') as f:
            json.dump(new_intel, f, indent=4)
            
        print(f"Successfully saved {len(new_intel)} real-world IoCs to {target_path}!")

    except Exception as e:
        print(f"Failed to fetch data: {e}")

def process_item(item, new_intel):
    # We only want IPs right now for the Triage Agent
    if item.get('ioc_type') == 'ip:port':
        # Split off the port (e.g. 192.168.1.1:8080 -> 192.168.1.1)
        raw_ip = item.get('ioc_value', '').split(':')[0]
        
        if not raw_ip:
            return

        # Build tags list
        tags = [item.get('threat_type', 'malicious')]
        actor = item.get('malware_printable') or item.get('malware')
        if actor:
            tags.append(actor.lower().replace(' ', '-'))

        # Format it exactly how your mock data expects it
        new_intel.append({
            "indicator": raw_ip,
            "type": "ip",
            "threat_score": item.get('confidence_level', 50),
            "tags": tags,
            "campaigns": [],
            "first_seen": item.get('first_seen_utc', ''),
            "last_seen": item.get('last_seen_utc', '')
        })

if __name__ == "__main__":
    update_threat_intel()
