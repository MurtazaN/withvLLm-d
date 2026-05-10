"""Kafka consumer for processing alerts from Kafka."""

import asyncio
import json
import logging
import os
import time
from typing import Optional

from aiokafka import AIOKafkaConsumer
from opentelemetry import metrics  # noqa: F401  (kept for direct OTel access)

from blue_lantern.pipeline import run_pipeline
from blue_lantern.connectors.output_gcp import upload_result
from blue_lantern.connectors.dlq_kafka import send_to_dlq
from blue_lantern.connectors.base import ErrorType
from blue_lantern.connectors.metrics import (
    record_alert_processed,
    record_alert_dropped,
    record_alert_dlq,
    record_kafka_message_consumed,
    record_processing_latency,
)

logger = logging.getLogger("blue_lantern.connectors.kafka_consumer")

# Configuration
BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC_ALERTS = os.environ.get("KAFKA_TOPIC_ALERTS", "blue-lantern-alerts")
TOPIC_DLQ = os.environ.get("KAFKA_TOPIC_DLQ", "blue-lantern-alerts-dlq")
CONSUMER_GROUP = os.environ.get("KAFKA_CONSUMER_GROUP", "blue-lantern-consumers")
MAX_POLL_RECORDS = 10
SESSION_TIMEOUT_MS = 30000
AUTO_OFFSET_RESET = "earliest"
PIPELINE_TIMEOUT = 300
RETRY_COUNT = 3
RETRY_DELAY = 30

# Global consumer instance
_consumer: Optional[AIOKafkaConsumer] = None
_consumer_task: Optional[asyncio.Task] = None
_running = False


async def get_kafka_consumer() -> Optional[AIOKafkaConsumer]:
    """Get or create Kafka consumer instance.

    Returns:
        Kafka consumer instance or None
    """
    global _consumer

    if _consumer is None:
        try:
            _consumer = AIOKafkaConsumer(
                bootstrap_servers=BOOTSTRAP_SERVERS,
                group_id=CONSUMER_GROUP,
                auto_offset_reset=AUTO_OFFSET_RESET,
                enable_auto_commit=False,  # Manual commit for reliability
                max_poll_records=MAX_POLL_RECORDS,
                session_timeout_ms=SESSION_TIMEOUT_MS,
                value_deserializer=lambda v: json.loads(v.decode()),
                key_deserializer=lambda k: k.decode(),
            )

            await _consumer.start()
            logger.info(
                f"Kafka consumer connected to {BOOTSTRAP_SERVERS}, "
                f"subscribed to {TOPIC_ALERTS}"
            )

        except Exception as e:
            logger.error(f"Failed to create Kafka consumer: {e}")
            _consumer = None

    return _consumer


async def shutdown_consumer():
    """Shutdown Kafka consumer gracefully."""
    global _consumer, _running

    _running = False

    if _consumer:
        try:
            await _consumer.stop()
            logger.info("Kafka consumer stopped")
        except Exception as e:
            logger.error(f"Failed to stop Kafka consumer: {e}")
        finally:
            _consumer = None


async def process_alert(alert: dict, source: str = "unknown") -> bool:
    """Process a single alert through the pipeline.

    Args:
        alert: Alert dict
        source: Source identifier

    Returns:
        True if processed successfully, False otherwise
    """
    # Retry logic for service unavailability
    for attempt in range(RETRY_COUNT):
        try:
            logger.info(
                f"Processing alert {alert.get('id', 'unknown')} from {source} "
                f"(attempt {attempt + 1}/{RETRY_COUNT})",
                extra={"alert_id": alert.get("id", "unknown"), "source": source},
            )

            # Run pipeline with timeout
            started_at = time.perf_counter()
            result = await asyncio.wait_for(
                run_pipeline(alert), timeout=PIPELINE_TIMEOUT
            )
            record_processing_latency(time.perf_counter() - started_at)

            # Upload result to GCP
            await upload_result(result)

            severity = (
                result.get("final_verdict", {}).get("verified_severity")
                or result.get("triage_result", {}).get("severity")
                or "unknown"
            )
            record_alert_processed(severity)

            logger.info(
                f"Successfully processed alert {alert.get('id', 'unknown')}",
                extra={"alert_id": alert.get("id", "unknown"), "source": source},
            )
            return True

        except asyncio.TimeoutError:
            logger.error(
                f"Pipeline timeout for alert {alert.get('id', 'unknown')}"
            )
            await send_to_dlq(
                alert,
                ErrorType.PIPELINE_TIMEOUT,
                "Pipeline timeout",
                source,
            )
            record_alert_dlq(ErrorType.PIPELINE_TIMEOUT.value)
            return False

        except Exception as e:
            error_str = str(e).lower()

            # Check if error is agent-related (requires manual intervention)
            if "agent" in error_str or "triage" in error_str or "verification" in error_str:
                logger.error(
                    f"Agent failure for alert {alert.get('id', 'unknown')}: {e}. "
                    "Stopping pipeline - requires manual intervention."
                )
                # Stop pipeline by raising exception
                raise RuntimeError(
                    f"Agent failure for alert {alert.get('id', 'unknown')}: {e}. "
                    "Pipeline stopped - requires manual intervention."
                )

            # Service not available - retry
            if attempt < RETRY_COUNT - 1:
                logger.warning(
                    f"Service unavailable for alert {alert.get('id', 'unknown')}: {e}. "
                    f"Retrying in {RETRY_DELAY} seconds..."
                )
                await asyncio.sleep(RETRY_DELAY)
            else:
                # Max retries exceeded - send to DLQ
                logger.error(
                    f"Max retries exceeded for alert {alert.get('id', 'unknown')}: {e}"
                )
                await send_to_dlq(
                    alert,
                    ErrorType.SERVICE_UNAVAILABLE,
                    f"Service unavailable after {RETRY_COUNT} retries: {e}",
                    source,
                )
                record_alert_dlq(ErrorType.SERVICE_UNAVAILABLE.value)
                return False


async def consume_alerts():
    """Consume alerts from Kafka and run pipeline."""
    global _running

    consumer = await get_kafka_consumer()
    if not consumer:
        logger.error("Kafka consumer not available, cannot consume alerts")
        return

    _running = True

    logger.info("Starting Kafka consumer")

    try:
        # Subscribe to alerts topic
        await consumer.subscribe([TOPIC_ALERTS])

        while _running:
            # Poll for messages
            async for message in consumer:
                try:
                    record_kafka_message_consumed(TOPIC_ALERTS)
                    alert = message.value
                    source = message.headers.get("source", "unknown") if message.headers else "unknown"

                    # Process alert
                    success = await process_alert(alert, source)

                    # Commit offset only after successful processing
                    if success:
                        await consumer.commit()

                except RuntimeError as e:
                    # Agent failure - stop pipeline
                    logger.error(f"Pipeline stopped due to agent failure: {e}")
                    _running = False
                    break
                except Exception as e:
                    record_alert_dropped("consumer_exception")
                    logger.error(f"Error processing message: {e}")
                    # Don't commit offset on failure
                    continue

    except asyncio.CancelledError:
        logger.info("Kafka consumer cancelled")
    except Exception as e:
        logger.error(f"Kafka consumer error: {e}")
    finally:
        _running = False


async def start_consumer():
    """Start the Kafka consumer in background."""
    global _consumer_task

    if _consumer_task and not _consumer_task.done():
        logger.warning("Kafka consumer already running")
        return

    # Hold a strong reference at module level so the task isn't GC'd
    # mid-flight (asyncio only weak-refs background tasks).
    _consumer_task = asyncio.create_task(consume_alerts())
    logger.info("Kafka consumer started in background")


async def stop_consumer():
    """Stop the Kafka consumer."""
    global _running, _consumer_task

    if not _consumer_task or _consumer_task.done():
        logger.warning("Kafka consumer not running")
        return

    _running = False
    _consumer_task.cancel()
    try:
        await _consumer_task
    except asyncio.CancelledError:
        pass
    _consumer_task = None
    await shutdown_consumer()
    logger.info("Kafka consumer stopped")
