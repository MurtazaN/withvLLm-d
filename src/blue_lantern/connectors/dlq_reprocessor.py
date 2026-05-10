"""DLQ reprocessor for automatic reprocessing of failed alerts."""

import asyncio
import json
import logging
import os
from typing import Optional

from aiokafka import AIOKafkaConsumer
from opentelemetry import metrics

from blue_lantern.pipeline import run_pipeline
from blue_lantern.connectors.output_gcp import upload_result
from blue_lantern.connectors.kafka_producer import get_kafka_producer

logger = logging.getLogger("blue_lantern.connectors.dlq_reprocessor")

# Configuration
BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC_DLQ = os.environ.get("KAFKA_TOPIC_DLQ", "blue-lantern-alerts-dlq")
CONSUMER_GROUP = "blue-lantern-dlq-reprocessor"
MAX_RETRIES = int(os.environ.get("ERROR_DLQ_MAX_RETRIES", "3"))
RETRY_INTERVAL = int(os.environ.get("ERROR_DLQ_RETRY_INTERVAL", "300"))  # 5 minutes

# Global consumer instance
_consumer: Optional[AIOKafkaConsumer] = None
_reprocessor_task: Optional[asyncio.Task] = None
_running = False


async def get_dlq_consumer() -> Optional[AIOKafkaConsumer]:
    """Get or create DLQ consumer instance.

    Returns:
        Kafka consumer instance or None
    """
    global _consumer

    if _consumer is None:
        try:
            _consumer = AIOKafkaConsumer(
                bootstrap_servers=BOOTSTRAP_SERVERS,
                group_id=CONSUMER_GROUP,
                auto_offset_reset="latest",
                enable_auto_commit=False,
                value_deserializer=lambda v: json.loads(v.decode()),
                key_deserializer=lambda k: k.decode(),
            )

            await _consumer.start()
            logger.info(
                f"DLQ consumer connected to {BOOTSTRAP_SERVERS}, "
                f"subscribed to {TOPIC_DLQ}"
            )

        except Exception as e:
            logger.error(f"Failed to create DLQ consumer: {e}")
            _consumer = None

    return _consumer


async def shutdown_dlq_consumer():
    """Shutdown DLQ consumer gracefully."""
    global _consumer, _running

    _running = False

    if _consumer:
        try:
            await _consumer.stop()
            logger.info("DLQ consumer stopped")
        except Exception as e:
            logger.error(f"Failed to stop DLQ consumer: {e}")
        finally:
            _consumer = None


async def reprocess_dlq():
    """Reprocess DLQ entries automatically."""
    global _running

    consumer = await get_dlq_consumer()
    if not consumer:
        logger.error("DLQ consumer not available, cannot reprocess")
        return

    _running = True

    logger.info("Starting DLQ reprocessor")

    try:
        # Subscribe to DLQ topic
        await consumer.subscribe([TOPIC_DLQ])

        while _running:
            # Poll for messages
            async for message in consumer:
                try:
                    entry = message.value
                    retry_count = entry.get("retry_count", 0)

                    alert_id = entry.get("original_event", {}).get("id", "unknown")

                    if retry_count >= MAX_RETRIES:
                        logger.error(
                            f"Max retries exceeded for alert {alert_id}, "
                            f"skipping reprocessing"
                        )
                        # Commit offset to skip this message
                        await consumer.commit()
                        continue

                    logger.info(
                        f"Reprocessing alert {alert_id} "
                        f"(attempt {retry_count + 1}/{MAX_RETRIES})"
                    )

                    # Reprocess alert
                    try:
                        result = await run_pipeline(entry["original_event"])
                        await upload_result(result)

                        # Success: don't put back in DLQ
                        logger.info(
                            f"Successfully reprocessed alert {alert_id}"
                        )

                        # Commit offset to mark as processed
                        await consumer.commit()

                    except Exception as e:
                        # Failure: increment retry count and put back in DLQ
                        logger.warning(
                            f"Reprocessing failed for alert {alert_id}: {e}"
                        )

                        entry["retry_count"] = retry_count + 1

                        # Send back to DLQ with updated retry count
                        producer = await get_kafka_producer()
                        if producer:
                            await producer.send_and_wait(
                                TOPIC_DLQ,
                                value=json.dumps(entry).encode(),
                                key=alert_id.encode(),
                            )

                        # Commit offset to mark as processed
                        await consumer.commit()

                except Exception as e:
                    logger.error(f"Error processing DLQ message: {e}")
                    # Don't commit offset on failure
                    continue

            # Wait before next check
            await asyncio.sleep(RETRY_INTERVAL)

    except asyncio.CancelledError:
        logger.info("DLQ reprocessor cancelled")
    except Exception as e:
        logger.error(f"DLQ reprocessor error: {e}")
    finally:
        _running = False


async def start_dlq_reprocessor():
    """Start the DLQ reprocessor in background."""
    global _reprocessor_task

    if _reprocessor_task and not _reprocessor_task.done():
        logger.warning("DLQ reprocessor already running")
        return

    # Hold a strong reference at module level so the task isn't GC'd
    # mid-flight (asyncio only weak-refs background tasks).
    _reprocessor_task = asyncio.create_task(reprocess_dlq())
    logger.info("DLQ reprocessor started in background")


async def stop_dlq_reprocessor():
    """Stop the DLQ reprocessor."""
    global _running, _reprocessor_task

    if not _reprocessor_task or _reprocessor_task.done():
        logger.warning("DLQ reprocessor not running")
        return

    _running = False
    _reprocessor_task.cancel()
    try:
        await _reprocessor_task
    except asyncio.CancelledError:
        pass
    _reprocessor_task = None
    await shutdown_dlq_consumer()
    logger.info("DLQ reprocessor stopped")
