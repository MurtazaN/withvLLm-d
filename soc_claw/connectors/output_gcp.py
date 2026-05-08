"""GCP Bucket output API for writing pipeline results."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.cloud import storage
from google.oauth2 import service_account

from soc_claw.connectors.metrics import (
    record_gcp_upload,
    record_gcp_upload_error,
    record_gcp_upload_latency,
)

logger = logging.getLogger("soc_claw.connectors.output_gcp")

# Configuration
BUCKET_NAME = os.environ.get("GCP_BUCKET_NAME", "soc-claw-results")
CREDENTIALS_PATH = os.environ.get("GCP_CREDENTIALS_PATH", "")
OUTPUT_FORMAT = os.environ.get("GCP_OUTPUT_FORMAT", "jsonl")
RETRY_COUNT = int(os.environ.get("GCP_UPLOAD_RETRY_COUNT", "3"))
RETRY_DELAY = int(os.environ.get("GCP_UPLOAD_RETRY_DELAY", "30"))

# Global GCP client
_gcp_client: Optional[storage.Client] = None


def get_gcp_client() -> Optional[storage.Client]:
    """Get or create GCP storage client.

    Returns:
        GCP storage client or None
    """
    global _gcp_client

    if _gcp_client is None:
        try:
            if CREDENTIALS_PATH:
                # Use service account credentials
                credentials = service_account.Credentials.from_service_account_file(
                    CREDENTIALS_PATH
                )
                _gcp_client = storage.Client(
                    project=credentials.project_id, credentials=credentials
                )
            else:
                # Use default credentials (ADC)
                _gcp_client = storage.Client()

            logger.info(f"GCP client initialized for bucket: {BUCKET_NAME}")

        except Exception as e:
            logger.error(f"Failed to create GCP client: {e}")
            _gcp_client = None

    return _gcp_client


async def upload_result(result: dict) -> bool:
    """Upload pipeline result to GCP Bucket.

    Args:
        result: Pipeline result dict

    Returns:
        True if uploaded successfully, False otherwise
    """
    client = get_gcp_client()
    if not client:
        logger.error("GCP client not available")
        return False

    try:
        alert_id = result.get("alert", {}).get("id", "unknown")
        timestamp = result.get("alert", {}).get("timestamp", datetime.now(timezone.utc).isoformat())

        # Generate file path based on timestamp
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        path = f"realtime/{dt.year:04d}/{dt.month:02d}/{dt.day:02d}/{dt.hour:02d}/alerts_{dt.strftime('%Y%m%d%H%M%S')}.jsonl"

        # Prepare content
        if OUTPUT_FORMAT == "jsonl":
            content = json.dumps(result) + "\n"
        else:
            content = json.dumps(result)

        # Upload with retry logic
        for attempt in range(RETRY_COUNT):
            try:
                bucket = client.bucket(BUCKET_NAME)
                blob = bucket.blob(path)

                # Upload content
                started_at = time.perf_counter()
                await asyncio.to_thread(
                    blob.upload_from_string, content, content_type="application/jsonl"
                )
                record_gcp_upload_latency(time.perf_counter() - started_at)
                record_gcp_upload(BUCKET_NAME)

                logger.info(
                    f"Uploaded result for alert {alert_id} to GCP: {path}",
                    extra={"alert_id": alert_id, "path": path},
                )
                return True

            except Exception as e:
                if attempt < RETRY_COUNT - 1:
                    logger.warning(
                        f"Upload attempt {attempt + 1} failed for alert {alert_id}, "
                        f"retrying in {RETRY_DELAY}s: {e}"
                    )
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    record_gcp_upload_error(BUCKET_NAME, type(e).__name__)
                    logger.error(
                        f"Failed to upload result for alert {alert_id} after "
                        f"{RETRY_COUNT} attempts: {e}"
                    )
                    return False

    except Exception as e:
        record_gcp_upload_error(BUCKET_NAME, type(e).__name__)
        logger.error(f"Failed to upload result for alert {alert_id}: {e}")
        return False


async def download_results(path: str) -> str:
    """Download results from GCP Bucket.

    Args:
        path: GCP object path

    Returns:
        File content
    """
    client = get_gcp_client()
    if not client:
        raise Exception("GCP client not available")

    try:
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(path)

        content = await asyncio.to_thread(blob.download_as_string)

        logger.info(f"Downloaded results from GCP: {path}")
        return content

    except Exception as e:
        logger.error(f"Failed to download results from GCP: {e}")
        raise


async def upload_dlq_entry(dlq_entry: dict) -> bool:
    """Upload DLQ entry to GCP Bucket.

    Args:
        dlq_entry: DLQ entry dict

    Returns:
        True if uploaded successfully, False otherwise
    """
    client = get_gcp_client()
    if not client:
        logger.error("GCP client not available for DLQ upload")
        return False

    try:
        alert_id = dlq_entry.get("original_event", {}).get("id", "unknown")
        ingested_at = dlq_entry.get("ingested_at", datetime.now(timezone.utc).isoformat())

        # Parse timestamp for path
        try:
            dt = datetime.fromisoformat(ingested_at.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)

        # Generate file path
        path = f"dlq/{dt.year:04d}/{dt.month:02d}/{dt.day:02d}/{dt.hour:02d}/dlq_{dt.strftime('%Y%m%d%H%M%S')}.jsonl"

        # Prepare content
        content = json.dumps(dlq_entry) + "\n"

        # Upload with retry logic
        for attempt in range(RETRY_COUNT):
            try:
                bucket = client.bucket(BUCKET_NAME)
                blob = bucket.blob(path)

                await asyncio.to_thread(
                    blob.upload_from_string, content, content_type="application/jsonl"
                )

                logger.info(
                    f"Uploaded DLQ entry for alert {alert_id} to GCP: {path}",
                    extra={"alert_id": alert_id, "path": path},
                )
                return True

            except Exception as e:
                if attempt < RETRY_COUNT - 1:
                    logger.warning(
                        f"DLQ upload attempt {attempt + 1} failed for alert {alert_id}, "
                        f"retrying in {RETRY_DELAY}s: {e}"
                    )
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    logger.error(
                        f"Failed to upload DLQ entry for alert {alert_id} after "
                        f"{RETRY_COUNT} attempts: {e}"
                    )
                    return False

    except Exception as e:
        logger.error(f"Failed to upload DLQ entry: {e}")
        return False
