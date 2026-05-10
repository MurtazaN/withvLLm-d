"""Job manager for batch API job tracking."""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from redis.asyncio import Redis

logger = logging.getLogger("blue-lantern.connectors.job_manager")


class JobStatus:
    """Job status enumeration."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobManager:
    """Manager for batch API job tracking."""

    def __init__(self, redis: Redis):
        """Initialize JobManager.

        Args:
            redis: Async Redis client
        """
        self.redis = redis
        self.key_prefix = "blue-lantern:job:"

    async def create_job(
        self,
        file_name: str,
        alert_count: int,
        source: str = "batch",
    ) -> str:
        """Create a new batch job.

        Args:
            file_name: Name of uploaded file
            alert_count: Number of alerts in file
            source: Source identifier

        Returns:
            Job ID
        """
        job_id = f"batch-{int(datetime.now(timezone.utc).timestamp())}"

        job_data = {
            "job_id": job_id,
            "file_name": file_name,
            "alert_count": alert_count,
            "source": source,
            "status": JobStatus.PENDING,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "processed_count": 0,
            "failed_count": 0,
            "results_location": None,
            "error_message": None,
        }

        key = f"{self.key_prefix}{job_id}"
        await self.redis.hset(key, mapping=job_data)
        await self.redis.expire(key, 86400)  # 24 hours TTL

        logger.info(f"Created job {job_id} for {alert_count} alerts from {file_name}")
        return job_id

    async def update_status(
        self,
        job_id: str,
        status: str,
        processed_count: int = None,
        failed_count: int = None,
        results_location: str = None,
        error_message: str = None,
    ) -> bool:
        """Update job status.

        Args:
            job_id: Job ID
            status: New status
            processed_count: Number of alerts processed
            failed_count: Number of alerts failed
            results_location: GCP path to results
            error_message: Error message if failed

        Returns:
            True if updated, False if job not found
        """
        key = f"{self.key_prefix}{job_id}"

        exists = await self.redis.exists(key)
        if not exists:
            logger.warning(f"Job {job_id} not found")
            return False

        updates = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if processed_count is not None:
            updates["processed_count"] = processed_count
        if failed_count is not None:
            updates["failed_count"] = failed_count
        if results_location is not None:
            updates["results_location"] = results_location
        if error_message is not None:
            updates["error_message"] = error_message

        await self.redis.hset(key, mapping=updates)
        logger.info(f"Updated job {job_id} to {status}")
        return True

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job details.

        Args:
            job_id: Job ID

        Returns:
            Job data or None if not found
        """
        key = f"{self.key_prefix}{job_id}"

        exists = await self.redis.exists(key)
        if not exists:
            return None

        job_data = await self.redis.hgetall(key)
        return job_data

    async def increment_processed(self, job_id: str) -> int:
        """Increment processed count for a job.

        Args:
            job_id: Job ID

        Returns:
            New processed count
        """
        key = f"{self.key_prefix}{job_id}"

        count = await self.redis.hincrby(key, "processed_count", 1)
        await self.redis.hset(key, mapping={"updated_at": datetime.now(timezone.utc).isoformat()})

        return count

    async def increment_failed(self, job_id: str) -> int:
        """Increment failed count for a job.

        Args:
            job_id: Job ID

        Returns:
            New failed count
        """
        key = f"{self.key_prefix}{job_id}"

        count = await self.redis.hincrby(key, "failed_count", 1)
        await self.redis.hset(key, mapping={"updated_at": datetime.now(timezone.utc).isoformat()})

        return count

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job record.

        Args:
            job_id: Job ID

        Returns:
            True if deleted, False if not found
        """
        key = f"{self.key_prefix}{job_id}"

        exists = await self.redis.exists(key)
        if not exists:
            return False

        await self.redis.delete(key)
        logger.info(f"Deleted job {job_id}")
        return True
