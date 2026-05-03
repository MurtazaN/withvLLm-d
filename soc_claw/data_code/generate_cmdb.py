import json
import random
from faker import Faker
import os

fake = Faker()

def generate_cmdb_dataset(num_assets=10000):
    print(f"Generating {num_assets} synthetic CMDB assets...")
    
    # Predefined lists to ensure realistic and consistent categorization
    departments = ["Human Resources", "Engineering Team", "Finance", "Marketing Team", "Sales", "Infrastructure Team", "Database Team", "Network Team", "Security Team", "Vendor Management", "Facilities"]
    
    os_list = {
        "server": ["Windows Server 2022", "Windows Server 2019", "Ubuntu 24.04 LTS", "Ubuntu 22.04 LTS", "Red Hat Enterprise Linux 9", "CentOS 8"],
        "workstation": ["Windows 11 Enterprise", "Windows 11 Pro", "Windows 10 Enterprise", "macOS 15.3", "macOS 14.2", "Ubuntu 24.04 LTS"],
        "network": ["Palo Alto PAN-OS 11.1", "Cisco IOS XE", "FortiOS 7.2", "F5 BIG-IP"],
        "iot": ["Embedded Linux", "VxWorks", "Custom Firmware"]
    }
    
    network_zones = ["corporate-endpoints", "corporate-core", "dmz", "data-tier", "guest-vlan", "contractor-vlan", "iot-vlan", "mgmt-vlan", "lab"]

    assets = []
    
    for i in range(num_assets):
        # Determine the type of asset
        asset_type = random.choices(
            ["workstation", "server", "network", "iot"], 
            weights=[0.70, 0.20, 0.05, 0.05] # 70% laptops, 20% servers, 5% routers/firewalls, 5% IoT
        )[0]
        
        # Base logic for generating the specific asset
        if asset_type == "workstation":
            prefix = random.choice(["WS", "LAPTOP", "DESKTOP"])
            dept_abbr = random.choice(["HR", "DEV", "MKTG", "SALES", "FIN"])
            hostname = f"{prefix}-{dept_abbr}-{i:05d}"
            criticality = random.choices(["medium", "low"], weights=[0.8, 0.2])[0]
            business_function = f"{random.choice(departments)} workstation for {fake.job().lower()}"
            owner = random.choice(departments)
            os_name = random.choice(os_list["workstation"])
            zone = random.choice(["corporate-endpoints", "guest-vlan", "contractor-vlan"])
            
        elif asset_type == "server":
            prefix = random.choice(["SRV", "DB", "DC", "APP"])
            hostname = f"{prefix}-{fake.word().upper()}-{i:05d}"
            criticality = random.choices(["critical", "high", "medium"], weights=[0.3, 0.5, 0.2])[0]
            business_function = f"Application server for {fake.bs()}"
            owner = random.choice(["Infrastructure Team", "Database Team", "Security Team"])
            os_name = random.choice(os_list["server"])
            zone = random.choice(["corporate-core", "data-tier", "dmz", "mgmt-vlan"])
            
        elif asset_type == "network":
            prefix = random.choice(["FW", "RT", "SW", "VPN"])
            hostname = f"{prefix}-{fake.word().upper()}-{i:05d}"
            criticality = random.choices(["critical", "high"], weights=[0.6, 0.4])[0]
            business_function = f"Core network infrastructure device ({prefix})"
            owner = "Network Team"
            os_name = random.choice(os_list["network"])
            zone = "mgmt-vlan"
            
        else: # iot
            prefix = random.choice(["PRT", "CAM", "SENS", "HVAC"])
            hostname = f"IOT-{prefix}-{i:05d}"
            criticality = "low"
            business_function = f"Smart facility device - {prefix}"
            owner = "Facilities"
            os_name = random.choice(os_list["iot"])
            zone = "iot-vlan"

        # Generate a patch date within the last 90 days
        last_patch = fake.date_between(start_date='-90d', end_date='today').isoformat()

        # Build the JSON object
        asset = {
            "hostname": hostname,
            "criticality": criticality,
            "business_function": business_function,
            "owner": owner,
            "os": os_name,
            "last_patch": last_patch,
            "network_zone": zone
        }
        
        assets.append(asset)
        
    # Inject a few specific known critical assets to ensure the benchmark SIEM alerts trigger P1s correctly
    critical_injections = [
        {"hostname": "DC-FINANCE-01", "criticality": "critical", "business_function": "Active Directory domain controller for finance", "owner": "Infrastructure Team", "os": "Windows Server 2022", "last_patch": "2026-04-10", "network_zone": "corporate-core"},
        {"hostname": "SRV-DB-01", "criticality": "critical", "business_function": "Primary SQL database server for customer data", "owner": "Database Team", "os": "Ubuntu 22.04 LTS", "last_patch": "2026-04-08", "network_zone": "data-tier"}
    ]
    assets.extend(critical_injections)

    # Save to file
    target_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'asset_inventory_data.json')
    
    # Create dir if not exists
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    
    with open(target_path, 'w') as f:
        json.dump(assets, f, indent=4)
        
    print(f"Successfully generated {len(assets)} assets and saved to {target_path}")

if __name__ == "__main__":
    generate_cmdb_dataset()
