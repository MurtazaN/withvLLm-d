"""Webhook endpoint for SIEM alert ingestion."""

import hmac
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException, Header, BackgroundTasks
from pydantic import BaseModel

from blue_lantern.connectors.base import (
    SIEMMapper,
    NormalizationError,
    ErrorType,
)
from blue_lantern.connectors.siem_splunk import SplunkMapper
from blue_lantern.connectors.siem_sentinel import SentinelMapper
from blue_lantern.connectors.siem_crowdstrike import CrowdStrikeMapper
from blue_lantern.connectors.kafka_producer import publish_alert
from blue_lantern.connectors.dlq_kafka import send_to_dlq
from blue_lantern.connectors.metrics import record_alert_ingested, record_alert_dlq
from blue_lantern.schemas import Alert

logger = logging.getLogger("blue_lantern.connectors.webhook")

router = APIRouter(prefix="/api/siem", tags=["siem"])

# Configuration
MAX_TIMESTAMP_AGE_SECONDS = 300  # 5 minutes

# Mapper registry
MAPPERS: Dict[str, SIEMMapper] = {
    "splunk": SplunkMapper(),
    "sentinel": SentinelMapper(),
    "crowdstrike": CrowdStrikeMapper(),
}


class WebhookResponse(BaseModel):
    """Webhook response model."""

    status: str
    message: str
    alert_id: Optional[str] = None


def verify_hmac(
    body: bytes, signature: str, secret: str, timestamp: str
) -> bool:
    """Verify HMAC-SHA256 signature.

    Args:
        body: Request body bytes
        signature: HMAC signature from header
        secret: Shared secret
        timestamp: Timestamp from header

    Returns:
        True if signature is valid
    """
    # Check timestamp age
    try:
        ts = int(timestamp)
        age = (datetime.now(timezone.utc).timestamp() - ts) / 60
        if age > MAX_TIMESTAMP_AGE_SECONDS / 60:
            logger.warning(f"Timestamp too old: {age:.1f} minutes")
            return False
    except (ValueError, TypeError):
        logger.warning("Invalid timestamp format")
        return False

    # Compute expected signature
    expected_sig = hmac.new(
        secret.encode(), f"{timestamp}.{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()

    # Use constant-time comparison
    return hmac.compare_digest(signature, expected_sig)


def get_siem_type(headers: Dict[str, str], body: Dict[str, Any]) -> str:
    """Detect SIEM type from headers or payload.

    Args:
        headers: Request headers
        body: Request body

    Returns:
        SIEM type identifier
    """
    # Check headers first
    siem_type = headers.get("X-SIEM-Type", headers.get("x-siem-type", ""))
    if siem_type:
        return siem_type.lower()

    # Try to detect from payload
    if "_time" in body or "_raw" in body:
        return "splunk"
    elif "properties" in body and "systemAlertId" in body:
        return "sentinel"
    elif "detection_id" in body and "composite" in body:
        return "crowdstrike"

    # Default to splunk
    return "splunk"


@router.post("/webhook", response_model=WebhookResponse)
async def siem_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: str = Header(..., alias="X-Signature"),
    x_timestamp: str = Header(..., alias="X-Timestamp"),
    x_siem_type: Optional[str] = Header(None, alias="X-SIEM-Type"),
):
    """Receive SIEM alert via webhook.

    Args:
        request: FastAPI request
        background_tasks: Background tasks
        x_signature: HMAC signature
        x_timestamp: Request timestamp
        x_siem_type: Optional SIEM type hint

    Returns:
        Webhook response
    """
    # Get request body
    body_bytes = await request.body()

    # Get tenant secret (in production, would be from k8s Secret)
    secret = os.environ.get("WEBHOOK_SECRET", "default-secret")

    # Verify HMAC
    if not verify_hmac(body_bytes, x_signature, secret, x_timestamp):
        logger.warning("Invalid HMAC signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse JSON
    try:
        raw_event = json.loads(body_bytes.decode())
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        await send_to_dlq(
            {"body": body_bytes.decode()},
            ErrorType.INVALID_JSON,
            str(e),
            "webhook",
        )
        record_alert_dlq(ErrorType.INVALID_JSON.value)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Detect SIEM type
    headers_dict = dict(request.headers)
    siem_type = get_siem_type(headers_dict, raw_event)

    # Get mapper
    mapper = MAPPERS.get(siem_type)
    if not mapper:
        logger.error(f"Unknown SIEM type: {siem_type}")
        await send_to_dlq(
            raw_event,
            ErrorType.NORMALIZATION_FAILURE,
            f"Unknown SIEM type: {siem_type}",
            siem_type,
        )
        record_alert_dlq(ErrorType.NORMALIZATION_FAILURE.value)
        raise HTTPException(status_code=400, detail=f"Unknown SIEM type: {siem_type}")

    source = mapper.extract_source(raw_event)

    # Normalize
    try:
        alert = mapper.normalize(raw_event)
    except NormalizationError as e:
        logger.error(f"Normalization failed: {e}")
        await send_to_dlq(raw_event, ErrorType.NORMALIZATION_FAILURE, str(e), source)
        record_alert_dlq(ErrorType.NORMALIZATION_FAILURE.value)
        raise HTTPException(status_code=400, detail="Normalization failed")

    # Validate schema
    try:
        Alert.model_validate(alert)
    except Exception as e:
        logger.error(f"Schema validation failed: {e}")
        await send_to_dlq(alert, ErrorType.SCHEMA_VALIDATION, str(e), source)
        record_alert_dlq(ErrorType.SCHEMA_VALIDATION.value)
        raise HTTPException(status_code=400, detail="Schema validation failed")

    # Publish to Kafka
    try:
        success = await publish_alert(alert, source)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to publish to Kafka")
    except Exception as e:
        logger.error(f"Failed to publish alert: {e}")
        await send_to_dlq(alert, ErrorType.SERVICE_UNAVAILABLE, str(e), source)
        record_alert_dlq(ErrorType.SERVICE_UNAVAILABLE.value)
        raise HTTPException(status_code=500, detail="Failed to publish to Kafka")

    record_alert_ingested(source, alert["id"])
    logger.info(
        f"Alert ingested via webhook: {alert['id']} from {source}",
        extra={"alert_id": alert["id"], "source": source},
    )

    return WebhookResponse(
        status="success", message="Alert ingested successfully", alert_id=alert["id"]
    )


@router.get("/health")
async def webhook_health():
    """Health check endpoint."""
    return {"status": "healthy"}
