"""Tests for GCP output."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from blue_lantern.connectors.output_gcp import (
    get_gcp_client,
    upload_result,
    shutdown_gcp_client,
)


@pytest.mark.asyncio
class TestGCPClient:
    """Tests for GCP client."""

    @pytest.fixture
    async def mock_client(self):
        """Create mock GCP client."""
        client = AsyncMock()
        client.bucket = MagicMock()
        client.bucket.return_value.blob = MagicMock()
        client.bucket.return_value.blob.return_value.upload_from_string = AsyncMock()
        return client

    async def test_get_gcp_client(self, mock_client):
        """Test getting GCP client instance."""
        with patch("blue_lantern.connectors.output_gcp.storage.Client", return_value=mock_client):
            client = get_gcp_client()
            assert client is not None

    async def test_upload_result_realtime(self, mock_client):
        """Test uploading realtime result."""
        result = {
            "alert": {
                "id": "ALT-001",
                "timestamp": "2026-04-25T14:32:00Z",
            },
            "triage_result": {
                "severity": "P1",
            },
            "final_verdict": {
                "verified_severity": "P1",
            },
            "response_plan": {
                "response_plan": [],
            },
        }

        with patch("blue_lantern.connectors.output_gcp.get_gcp_client", return_value=mock_client):
            await upload_result(result)
            mock_client.bucket.assert_called_once()
            mock_client.bucket.return_value.blob.assert_called_once()
            mock_client.bucket.return_value.blob.return_value.upload_from_string.assert_called_once()

    async def test_upload_result_batch(self, mock_client):
        """Test uploading batch result."""
        result = {
            "alert": {
                "id": "ALT-001",
                "timestamp": "2026-04-25T14:32:00Z",
            },
            "triage_result": {
                "severity": "P1",
            },
            "final_verdict": {
                "verified_severity": "P1",
            },
            "response_plan": {
                "response_plan": [],
            },
            "job_id": "batch-job-123",
        }

        with patch("blue_lantern.connectors.output_gcp.get_gcp_client", return_value=mock_client):
            await upload_result(result)
            mock_client.bucket.assert_called_once()
            mock_client.bucket.return_value.blob.assert_called_once()
            mock_client.bucket.return_value.blob.return_value.upload_from_string.assert_called_once()

    async def test_upload_result_failure(self, mock_client):
        """Test uploading result failure."""
        result = {
            "alert": {
                "id": "ALT-001",
                "timestamp": "2026-04-25T14:32:00Z",
            },
            "triage_result": {
                "severity": "P1",
            },
            "final_verdict": {
                "verified_severity": "P1",
            },
            "response_plan": {
                "response_plan": [],
            },
        }

        mock_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = Exception("GCP error")

        with patch("blue_lantern.connectors.output_gcp.get_gcp_client", return_value=mock_client):
            with pytest.raises(Exception):
                await upload_result(result)

    async def test_shutdown_gcp_client(self, mock_client):
        """Test shutting down GCP client."""
        with patch("blue_lantern.connectors.output_gcp._client", mock_client):
            await shutdown_gcp_client()
