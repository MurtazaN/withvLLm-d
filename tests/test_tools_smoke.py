from soc_claw.tools.asset_lookup import asset_lookup
from soc_claw.tools.ip_reputation import ip_reputation
from soc_claw.tools.mitre_lookup import mitre_lookup
from soc_claw.tools.response_tools import block_ioc, create_ticket, escalate, isolate_host


def test_ip_reputation_known_malicious():
    result = ip_reputation("185.220.101.42")
    assert result["verdict"] == "malicious"
    assert result["threat_score"] == 95


def test_ip_reputation_unknown():
    result = ip_reputation("8.8.8.8")
    assert result["verdict"] == "unknown"
    assert result["threat_score"] == 0


def test_asset_lookup_known_host():
    result = asset_lookup("DC-FINANCE-01")
    assert result["found"] is True
    assert result["criticality"] == "critical"


def test_asset_lookup_case_insensitive():
    result = asset_lookup("dc-finance-01")
    assert result["found"] is True


def test_asset_lookup_unknown_host():
    result = asset_lookup("UNKNOWN-HOST-999")
    assert result["found"] is False
    assert result["criticality"] == "medium"


def test_mitre_lookup_powershell():
    result = mitre_lookup("powershell encoded command downloading payload from external IP")
    assert any(r["technique_id"] == "T1059.001" for r in result)


def test_mitre_lookup_brute_force():
    result = mitre_lookup("brute force failed login authentication attempts password guessing")
    assert any(r["technique_id"] == "T1110.001" for r in result)


def test_mitre_lookup_dns_tunneling():
    result = mitre_lookup("dns tunneling query subdomain exfil covert channel")
    assert any(r["technique_id"] == "T1071.004" for r in result)


def test_response_tools_isolate_host():
    result = isolate_host("DC-FINANCE-01")
    assert result["status"] == "success"


def test_response_tools_block_ioc():
    result = block_ioc("185.220.101.42", "ip")
    assert result["status"] == "success"


def test_response_tools_create_ticket():
    result = create_ticket("P1 incident on DC-FINANCE-01", "critical")
    assert result["status"] == "success"
    assert result["ticket_id"].startswith("INC-")


def test_response_tools_escalate():
    result = escalate(3, "Active C2 communication on domain controller")
    assert result["status"] == "success"
