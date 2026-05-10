"""Tests for job manager."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from blue_lantern.connectors.job_manager import (
    JobManager,
    JobStatus,
)


@pytest.mark.asyncio
class TestJobManager:
    """Tests for job manager."""

    @pytest.fixture
    async def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.set = AsyncMock()
        redis.get = AsyncMock()
        redis.hset = AsyncMock()
        redis.hget = AsyncMock()
        redis.hgetall = AsyncMock()
        redis.hincrby = AsyncMock()
        redis.expire = AsyncMock()
        return redis

    @pytest.fixture
    async def job_manager(self, mock_redis):
        """Create job manager with mock Redis."""
        return JobManager(mock_redis)

    async def test_create_job(self, job_manager, mock_redis):
        """Test creating a new job."""
        job_id = await job_manager.create_job(
            filename="test.jsonl",
            total_alerts=10,
        )

        assert job_id is not None
        assert isinstance(job_id, str)
        mock_redis.hset.assert_called()

    async def test_get_job(self, job_manager, mock_redis):
        """Test getting job status."""
        job_id = "job-123"
        mock_redis.hgetall.return_value = {
            "status": "processing",
            "filename": "test.jsonl",
            "total_alerts": "10",
            "processed_alerts": "5",
            "failed_alerts": "0",
            "created_at": "2026-04-25T14:32:00Z",
            "updated_at": "2026-04-25T14:33:00Z",
        }

        job = await job_manager.get_job(job_id)

        assert job is not None
        assert job["status"] == "processing"
        assert job["filename"] == "test.jsonl"
        assert job["total_alerts"] == 10
        assert job["processed_alerts"] == 5
        assert job["failed_alerts"] == 0

    async def test_get_job_not_found(self, job_manager, mock_redis):
        """Test getting non-existent job."""
        mock_redis.hgetall.return_value = {}

        job = await job_manager.get_job("non-existent")

        assert job is None

    async def test_update_job_status(self, job_manager, mock_redis):
        """Test updating job status."""
        job_id = "job-123"

        await job_manager.update_job_status(job_id, JobStatus.COMPLETED)

        mock_redis.hset.assert_called()
        mock_redis.hget.assert_called()

    async def test_increment_processed(self, job_manager, mock_redis):
        """Test incrementing processed count."""
        job_id = "job-123"

        await job_manager.increment_processed(job_id)

        mock_redis.hincrby.assert_called_with(
            f"job:{job_id}", "processed_alerts", 1
        )

    async def test_increment_failed(self, job_manager, mock_redis):
        """Test incrementing failed count."""
        job_id = "job-123"

        await job_manager.increment_failed(job_id)

        mock_redis.hincrby.assert_called_with(
            f"job:{job_id}", "failed_alerts", 1
        )

    async def test_set_results_path(self, job_manager, mock_redis):
        """Test setting results path."""
        job_id = "job-123"
        results_path = "gs://bucket/results/job-123.json"

        await job_manager.set_results_path(job_id, results_path)

        mock_redis.hset.assert_called_with(
            f"job:{job_id}", "results_path", results_path
        )

    async def test_list_jobs(self, job_manager, mock_redis):
        """Test listing jobs."""
        mock_redis.keys.return_value = ["job:123", "job:456"]
        mock_redis.hgetall.side_effect = [
            {
                "status": "completed",
                "filename": "test1.jsonl",
                "total_alerts": "10",
                "processed_alerts": "10",
                "failed_alerts": "0",
            },
            {
                "status": "processing",
                "filename": "test2.jsonl",
                "total_alerts": "20",
                "processed_alerts": "15",
                "failed_alerts": "1",
            },
        ]

        jobs = await job_manager.list_jobs()

        assert len(jobs) == 2
        assert jobs[0]["status"] == "completed"
        assert jobs[1]["status"] == "processing"

    async def test_delete_job(self, job_manager, mock_redis):
        """Test deleting a job."""
        job_id = "job-123"

        await job_manager.delete_job(job_id)

        mock_redis.delete.assert_called_with(f"job:{job_id}")
