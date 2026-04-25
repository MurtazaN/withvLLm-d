import random
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def isolate_host(hostname: str) -> dict:
    """Simulate network isolation via EDR API."""
    print(f"[EDR] {_now()} Isolating host: {hostname}")
    return {
        "status": "success",
        "action": "host_isolated",
        "hostname": hostname,
        "timestamp": _now(),
    }


def block_ioc(indicator: str, indicator_type: str) -> dict:
    """Simulate blocking an IOC at network perimeter."""
    print(f"[FIREWALL] {_now()} Blocking {indicator_type}: {indicator}")
    return {
        "status": "success",
        "action": "ioc_blocked",
        "indicator": indicator,
        "type": indicator_type,
        "timestamp": _now(),
    }


def create_ticket(summary: str, priority: str) -> dict:
    """Simulate creating an ITSM ticket."""
    ticket_id = f"INC-{datetime.now().strftime('%Y%m%d')}-{random.randint(1, 999):03d}"
    print(f"[ITSM] {_now()} Ticket created: {ticket_id} [{priority}] {summary}")
    return {
        "status": "success",
        "action": "ticket_created",
        "ticket_id": ticket_id,
        "summary": summary,
        "priority": priority,
        "timestamp": _now(),
    }


def escalate(tier: int, message: str) -> dict:
    """Simulate escalation to higher-tier analyst."""
    print(f"[ESCALATION] {_now()} Escalated to Tier {tier}: {message}")
    return {
        "status": "success",
        "action": "escalated",
        "escalated_to": f"Tier {tier}",
        "message": message,
        "timestamp": _now(),
    }


if __name__ == "__main__":
    print("--- Testing response_tools ---\n")

    result = isolate_host("DC-FINANCE-01")
    print(f"Result: {result}\n")
    assert result["status"] == "success"

    result = block_ioc("185.220.101.42", "ip")
    print(f"Result: {result}\n")
    assert result["status"] == "success"

    result = create_ticket("P1 incident on DC-FINANCE-01", "critical")
    print(f"Result: {result}\n")
    assert result["status"] == "success"
    assert result["ticket_id"].startswith("INC-")

    result = escalate(3, "Active C2 communication on domain controller")
    print(f"Result: {result}\n")
    assert result["status"] == "success"

    print("All response_tools tests passed!")
