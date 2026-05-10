"""Kafka producer for publishing alerts."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from aiokafka import AIOKafkaProducer
from opentelemetry import metrics  # noqa: F401  (kept for direct OTel access)

from blue_lantern.connectors.metrics import (
    record_kafka_message_published,
    record_kafka_publish_error,
)

logger = logging.getLogger("blue_lantern.connectors.kafka_producer")

# Global producer instance
_producer: Optional[AIOKafkaProducer] = None


async def get_kafka_producer() -> Optional[AIOKafkaProducer]:
    """Get or create Kafka producer instance.

    Returns:
        Kafka producer instance or None
    """
    global _producer

    if _producer is None:
        try:
            bootstrap_servers = os.environ.get(
                "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
            )

            _producer = AIOKafkaProducer(
                bootstrap_servers=bootstrap_servers,
                value_serializer=lambda v: v if isinstance(v, bytes) else v.encode(),
                key_serializer=lambda k: k if isinstance(k, bytes) else k.encode(),
                compression_type="gzip",
                acks="all",
                retries=3,
            )

            # Wait for producer to be ready
            await _producer.start()
            logger.info(f"Kafka producer connected to {bootstrap_servers}")

        except Exception as e:
            logger.error(f"Failed to create Kafka producer: {e}")
            _producer = None

    return _producer


async def shutdown_producer():
    """Shutdown Kafka producer gracefully."""
    global _producer

    if _producer:
        try:
            await _producer.stop()
            logger.info("Kafka producer stopped")
        except Exception as e:
            logger.error(f"Failed to stop Kafka producer: {e}")
        finally:
            _producer = None


async def publish_alert(alert: dict, source: str = "unknown") -> bool:
    """Publish alert to Kafka topic.

    Args:
        alert: Alert dict
        source: Source identifier

    Returns:
        True if published successfully, False otherwise
    """
    producer = await get_kafka_producer()
    if not producer:
        logger.error("Kafka producer not available")
        return False

    try:
        await producer.send_and_wait(
            "blue-lantern-alerts",
            value=json.dumps(alert).encode(),
            key=alert["id"].encode(),
        )
        record_kafka_message_published("blue-lantern-alerts")
        logger.info(
            f"Published alert {alert['id']} from {source} to Kafka",
            extra={"alert_id": alert["id"], "source": source},
        )
        return True
    except Exception as e:
        record_kafka_publish_error("blue-lantern-alerts", type(e).__name__)
        logger.error(f"Failed to publish alert {alert.get('id', 'unknown')}: {e}")
        return False


async def publish_to_dlq(
    alert: dict,
    error_type: str,
    error_message: str,
    source: str = "unknown",
) -> bool:
    """Publish failed alert to DLQ topic.

    Args:
        alert: Alert dict
        error_type: Type of error
        error_message: Error message
        source: Source identifier

    Returns:
        True if published successfully, False otherwise
    """
    producer = await get_kafka_producer()
    if not producer:
        logger.error("Kafka producer not available for DLQ")
        return False

    try:
        dlq_entry = {
            "original_event": alert,
            "error_type": error_type,
            "error_message": error_message,
            "siem_source": source,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "retry_count": 0,
        }

        await producer.send_and_wait(
            "blue-lantern-alerts-dlq",
            value=json.dumps(dlq_entry).encode(),
            key=alert.get("id", "unknown").encode(),
        )
        record_kafka_message_published("blue-lantern-alerts-dlq")
        logger.error(
            f"Sent alert {alert.get('id', 'unknown')} to DLQ: {error_type}",
            extra={"alert_id": alert.get("id", "unknown"), "error_type": error_type},
        )
        return True
    except Exception as e:
        record_kafka_publish_error("blue-lantern-alerts-dlq", type(e).__name__)
        logger.error(f"Failed to send alert to DLQ: {e}")
        return False
