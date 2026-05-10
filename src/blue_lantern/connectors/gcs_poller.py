"""
GCS Poller Module

Background task to poll GCS for new alerts and process them.
"""

import asyncio
import logging
import os
from typing import Optional

from blue_lantern.connectors.gcs_reader import download_batch
from blue_lantern.pipeline import run_pipeline
from blue_lantern.connectors.output_gcp import upload_result

logger = logging.getLogger("blue-lantern.gcs_poller")

# Configuration
POLL_INTERVAL = int(os.environ.get("BLUE_LANTERN_GCS_POLL_INTERVAL", "300"))  # 5 minutes default
BATCH_SIZE = int(os.environ.get("BLUE_LANTERN_BATCH_SIZE", "30"))
GCS_LOG_BUCKET_NAME = os.environ.get("GCS_LOG_BUCKET_NAME", "")

# Poller task reference
_poller_task: Optional[asyncio.Task] = None


async def poll_gcs():
    """Background task to poll GCS for new alerts and process them."""
    if POLL_INTERVAL == 0:
        logger.info("GCS polling disabled (BLUE_LANTERN_GCS_POLL_INTERVAL=0)")
        return

    if not GCS_LOG_BUCKET_NAME:
        logger.warning("GCS polling disabled (GCS_LOG_BUCKET_NAME not set)")
        return

    logger.info(f"Starting GCS poller (interval={POLL_INTERVAL}s, batch_size={BATCH_SIZE}, bucket={GCS_LOG_BUCKET_NAME})")

    while True:
        try:
            alerts = download_batch(GCS_LOG_BUCKET_NAME, BATCH_SIZE)
            logger.info(f"Downloaded {len(alerts)} alerts from GCS")

            for alert in alerts:
                try:
                    result = await run_pipeline(alert)
                    await upload_result(result)
                    logger.info(f"Processed alert {alert.get('id')}")
                except Exception as e:
                    logger.error(f"Failed to process alert {alert.get('id')}: {e}")

        except Exception as e:
            logger.error(f"GCS poller failed: {e}")

        await asyncio.sleep(POLL_INTERVAL)


async def start_gcs_poller():
    """Start the GCS poller background task."""
    global _poller_task
    if POLL_INTERVAL > 0 and GCS_LOG_BUCKET_NAME:
        _poller_task = asyncio.create_task(poll_gcs())
        logger.info("GCS poller started")
    else:
        logger.info("GCS poller not started (disabled or not configured)")


async def stop_gcs_poller():
    """Stop the GCS poller."""
    global _poller_task
    if _poller_task:
        _poller_task.cancel()
        try:
            await _poller_task
        except asyncio.CancelledError:
            pass
        _poller_task = None
        logger.info("GCS poller stopped")
