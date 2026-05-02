import logging
import random
from datetime import datetime, timezone

logger = logging.getLogger("soc-claw.tools.response_tools")


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def isolate_host(hostname: str) -> dict:
    """Simulate network isolation via EDR API."""
    logger.info(
        "action_executed",
        extra={
            "event": "action_executed",
            "action_type": "isolate_host",
            "target": hostname,
            "channel": "EDR",
        },
    )
    return {
        "status": "success",
        "action": "host_isolated",
        "hostname": hostname,
        "timestamp": _utc_timestamp(),
    }


def block_ioc(indicator: str, indicator_type: str) -> dict:
    """Simulate blocking an IOC at network perimeter."""
    logger.info(
        "action_executed",
        extra={
            "event": "action_executed",
            "action_type": "block_ioc",
            "target": indicator,
            "indicator_type": indicator_type,
            "channel": "FIREWALL",
        },
    )
    return {
        "status": "success",
        "action": "ioc_blocked",
        "indicator": indicator,
        "type": indicator_type,
        "timestamp": _utc_timestamp(),
    }


def create_ticket(summary: str, priority: str) -> dict:
    """Simulate creating an ITSM ticket."""
    ticket_id = f"INC-{datetime.now().strftime('%Y%m%d')}-{random.randint(1, 999):03d}"
    logger.info(
        "action_executed",
        extra={
            "event": "action_executed",
            "action_type": "create_ticket",
            "ticket_id": ticket_id,
            "priority": priority,
            "summary": summary,
            "channel": "ITSM",
        },
    )
    return {
        "status": "success",
        "action": "ticket_created",
        "ticket_id": ticket_id,
        "summary": summary,
        "priority": priority,
        "timestamp": _utc_timestamp(),
    }


def escalate(tier: int, message: str) -> dict:
    """Simulate escalation to higher-tier analyst."""
    logger.info(
        "action_executed",
        extra={
            "event": "action_executed",
            "action_type": "escalate",
            "tier": tier,
            "message": message,
            "channel": "ESCALATION",
        },
    )
    return {
        "status": "success",
        "action": "escalated",
        "escalated_to": f"Tier {tier}",
        "message": message,
        "timestamp": _utc_timestamp(),
    }


