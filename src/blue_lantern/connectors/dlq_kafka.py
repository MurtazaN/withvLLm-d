"""Kafka-based DLQ handler for failed alerts."""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from aiokafka import AIOKafkaProducer
from opentelemetry import metrics

from blue_lantern.connectors.kafka_producer import get_kafka_producer
from blue_lantern.connectors.base import ErrorType

logger = logging.getLogger("blue_lantern.connectors.dlq_kafka")

# Configuration
TOPIC_DLQ = os.environ.get("KAFKA_TOPIC_DLQ", "blue-lantern-alerts-dlq")
RETRY_COUNT = int(os.environ.get("ERROR_DLQ_MAX_RETRIES", "3"))
RETRY_DELAY = int(os.environ.get("ERROR_DLQ_RETRY_DELAY", "60"))


async def send_to_dlq(
    alert: dict,
    error_type: ErrorType,
    error_message: str,
    source: str = "unknown",
) -> bool:
    """Send failed alert to DLQ topic.

    Args:
        alert: Alert dict
        error_type: Type of error
        error_message: Error message
        source: Source identifier

    Returns:
        True if sent successfully, False otherwise
    """
    producer = await get_kafka_producer()
    if not producer:
        logger.error("Kafka producer not available for DLQ")
        return False

    try:
        dlq_entry = {
            "original_event": alert,
            "error_type": error_type.value,
            "error_message": error_message,
            "siem_source": source,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "retry_count": 0,
        }

        await producer.send_and_wait(
            TOPIC_DLQ,
            value=json.dumps(dlq_entry).encode(),
            key=alert.get("id", "unknown").encode(),
        )
        logger.error(
            f"Sent alert {alert.get('id', 'unknown')} to DLQ: {error_type.value}",
            extra={"alert_id": alert.get("id", "unknown"), "error_type": error_type.value},
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send alert to DLQ: {e}")
        return False
