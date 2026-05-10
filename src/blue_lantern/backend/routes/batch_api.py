"""Batch API endpoint for JSONL file uploads."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, UploadFile, HTTPException
from pydantic import BaseModel

from blue_lantern.connectors.job_manager import JobManager, JobStatus
from blue_lantern.connectors.siem_splunk import SplunkMapper
from blue_lantern.connectors.siem_sentinel import SentinelMapper
from blue_lantern.connectors.siem_crowdstrike import CrowdStrikeMapper
from blue_lantern.connectors.base import NormalizationError
from blue_lantern.schemas import Alert

logger = logging.getLogger("blue-lantern.connectors.batch_api")

router = APIRouter(prefix="/api/batch", tags=["batch"])

# Mapper registry
MAPPERS = {
    "splunk": SplunkMapper(),
    "sentinel": SentinelMapper(),
    "crowdstrike": CrowdStrikeMapper(),
}


class BatchUploadResponse(BaseModel):
    """Response for batch upload."""

    job_id: str
    status: str
    message: str
    alert_count: int


class JobStatusResponse(BaseModel):
    """Response for job status query."""

    job_id: str
    status: str
    file_name: str
    alert_count: int
    processed_count: int
    failed_count: int
    created_at: str
    updated_at: str
    results_location: Optional[str] = None
    error_message: Optional[str] = None


def detect_siem_type(alert: dict) -> str:
    """Detect SIEM type from alert structure.

    Args:
        alert: Alert dict

    Returns:
        SIEM type identifier
    """
    # Try to detect from payload structure
    if "_time" in alert or "_raw" in alert:
        return "splunk"
    elif "properties" in alert and "systemAlertId" in alert:
        return "sentinel"
    elif "detection_id" in alert and "composite" in alert:
        return "crowdstrike"

    # Default to splunk
    return "splunk"


@router.post("/upload", response_model=BatchUploadResponse)
async def upload_batch(file: UploadFile):
    """Upload JSONL file for batch processing.

    Args:
        file: JSONL file to upload

    Returns:
        Job ID and status
    """
    # Validate file type
    if not file.filename.endswith(".jsonl"):
        raise HTTPException(
            status_code=400,
            detail="File must be a JSONL file (.jsonl extension)",
        )

    # Read and parse file
    try:
        content = await file.read()
        lines = content.decode("utf-8").strip().split("\n")
        lines = [line.strip() for line in lines if line.strip()]
    except Exception as e:
        logger.error(f"Failed to read file: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    if not lines:
        raise HTTPException(status_code=400, detail="File is empty")

    # Parse JSONL
    alerts = []
    parse_errors = []

    for i, line in enumerate(lines):
        try:
            alert = json.loads(line)
            alerts.append(alert)
        except json.JSONDecodeError as e:
            parse_errors.append(f"Line {i + 1}: {e}")

    if parse_errors:
        logger.warning(f"Parse errors in {len(parse_errors)} lines: {parse_errors[:5]}")

    if not alerts:
        raise HTTPException(
            status_code=400,
            detail="No valid alerts found in file",
        )

    # Get Redis client (injected via dependency injection in production)
    from blue_lantern.backend.server import app

    redis = getattr(app.state, "redis", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    job_manager = JobManager(redis)

    # Create job
    job_id = await job_manager.create_job(
        file_name=file.filename,
        alert_count=len(alerts),
        source="batch",
    )

    # Update job status to processing
    await job_manager.update_status(job_id, JobStatus.PROCESSING)

    # Publish alerts to Kafka
    from blue_lantern.connectors.kafka_producer import get_kafka_producer

    producer = await get_kafka_producer()
    if not producer:
        await job_manager.update_status(
            job_id,
            JobStatus.FAILED,
            error_message="Kafka producer not available",
        )
        raise HTTPException(status_code=503, detail="Kafka producer not available")

    published_count = 0
    failed_count = 0

    for alert in alerts:
        try:
            # Detect SIEM type and normalize
            siem_type = detect_siem_type(alert)
            mapper = MAPPERS.get(siem_type)

            if mapper:
                normalized = mapper.normalize(alert)
                Alert.model_validate(normalized)
                alert = normalized

            # Publish to Kafka
            await producer.send_and_wait(
                "blue-lantern-alerts",
                value=json.dumps(alert).encode(),
                key=alert["id"].encode(),
            )

            await job_manager.increment_processed(job_id)
            published_count += 1

        except Exception as e:
            logger.error(f"Failed to publish alert {alert.get('id', 'unknown')}: {e}")
            await job_manager.increment_failed(job_id)
            failed_count += 1

    # Update job status
    if failed_count == 0:
        await job_manager.update_status(
            job_id,
            JobStatus.COMPLETED,
            processed_count=published_count,
        )
    else:
        await job_manager.update_status(
            job_id,
            JobStatus.FAILED,
            processed_count=published_count,
            failed_count=failed_count,
            error_message=f"{failed_count} alerts failed to publish",
        )

    logger.info(
        f"Batch upload completed: {job_id}, "
        f"published={published_count}, failed={failed_count}"
    )

    return BatchUploadResponse(
        job_id=job_id,
        status="completed" if failed_count == 0 else "partial",
        message=f"Processed {published_count} alerts, {failed_count} failed",
        alert_count=len(alerts),
    )


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get batch job status.

    Args:
        job_id: Job ID

    Returns:
        Job status details
    """
    from blue_lantern.backend.server import app

    redis = getattr(app.state, "redis", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    job_manager = JobManager(redis)
    job_data = await job_manager.get_job(job_id)

    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return JobStatusResponse(**job_data)


@router.get("/results/{job_id}")
async def get_job_results(job_id: str):
    """Download batch job results.

    Args:
        job_id: Job ID

    Returns:
        Results file or error
    """
    from blue_lantern.backend.server import app

    redis = getattr(app.state, "redis", None)
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    job_manager = JobManager(redis)
    job_data = await job_manager.get_job(job_id)

    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if job_data["status"] != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} is not completed (status: {job_data['status']})",
        )

    results_location = job_data.get("results_location")
    if not results_location:
        raise HTTPException(
            status_code=404,
            detail=f"Results not available for job {job_id}",
        )

    # Read results from GCP Bucket
    from blue_lantern.connectors.output_gcp import download_results

    try:
        results = await download_results(results_location)
        return results
    except Exception as e:
        logger.error(f"Failed to download results for job {job_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to download results: {e}",
        )
