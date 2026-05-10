"""Tests for DLQ Kafka handler."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from blue_lantern.connectors.dlq_kafka import (
    send_to_dlq,
    get_dlq_producer,
    shutdown_dlq_producer,
)
from blue_lantern.connectors.base import ErrorType


@pytest.mark.asyncio
class TestDLQKafka:
    """Tests for DLQ Kafka handler."""

    @pytest.fixture
    async def mock_producer(self):
        """Create mock DLQ producer."""
        producer = AsyncMock()
        producer.start = AsyncMock()
        producer.stop = AsyncMock()
        producer.send_and_wait = AsyncMock()
        return producer

    async def test_get_dlq_producer(self, mock_producer):
        """Test getting DLQ producer instance."""
        with patch("blue_lantern.connectors.dlq_kafka.AIOKafkaProducer", return_value=mock_producer):
            producer = get_dlq_producer()
            assert producer is not None
            await producer.start.assert_called_once()

    async def test_send_to_dlq(self, mock_producer):
        """Test sending alert to DLQ."""
        raw_event = {
            "id": "ALT-001",
            "timestamp": "2026-04-25T14:32:00Z",
            "hostname": "DC-FINANCE-01",
        }
        error_type = ErrorType.SCHEMA_VALIDATION
        error_message = "Missing required field"
        source = "splunk"

        with patch("blue_lantern.connectors.dlq_kafka.get_dlq_producer", return_value=mock_producer):
            await send_to_dlq(raw_event, error_type, error_message, source)
            mock_producer.send_and_wait.assert_called_once()

            # Verify the DLQ entry structure
            call_args = mock_producer.send_and_wait.call_args
            value = call_args.kwargs.get("value") or call_args.args[1]
            entry = json.loads(value.decode())

            assert entry["original_event"] == raw_event
            assert entry["error_type"] == error_type.value
            assert entry["error_message"] == error_message
            assert entry["siem_source"] == source
            assert "ingested_at" in entry
            assert entry["retry_count"] == 0

    async def test_send_to_dlq_with_retry_count(self, mock_producer):
        """Test sending alert to DLQ with retry count."""
        raw_event = {
            "id": "ALT-001",
            "timestamp": "2026-04-25T14:32:00Z",
        }
        error_type = ErrorType.SERVICE_UNAVAILABLE
        error_message = "Kafka unavailable"
        source = "splunk"

        with patch("blue_lantern.connectors.dlq_kafka.get_dlq_producer", return_value=mock_producer):
            await send_to_dlq(raw_event, error_type, error_message, source, retry_count=2)
            mock_producer.send_and_wait.assert_called_once()

            # Verify the retry count
            call_args = mock_producer.send_and_wait.call_args
            value = call_args.kwargs.get("value") or call_args.args[1]
            entry = json.loads(value.decode())

            assert entry["retry_count"] == 2

    async def test_send_to_dlq_failure(self, mock_producer):
        """Test sending to DLQ failure."""
        raw_event = {"id": "ALT-001"}
        error_type = ErrorType.INVALID_JSON
        error_message = "Invalid JSON"
        source = "webhook"

        mock_producer.send_and_wait.side_effect = Exception("Kafka error")

        with patch("blue_lantern.connectors.dlq_kafka.get_dlq_producer", return_value=mock_producer):
            # Should not raise exception, just log error
            await send_to_dlq(raw_event, error_type, error_message, source)

    async def test_shutdown_dlq_producer(self, mock_producer):
        """Test shutting down DLQ producer."""
        with patch("blue_lantern.connectors.dlq_kafka._producer", mock_producer):
            await shutdown_dlq_producer()
            mock_producer.stop.assert_called_once()
