"""Tests for Kafka producer and consumer."""

import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from blue_lantern.connectors.kafka_producer import (
    get_kafka_producer,
    publish_alert,
    shutdown_producer,
)
from blue_lantern.connectors.kafka_consumer import (
    get_kafka_consumer,
    start_kafka_consumer,
    stop_kafka_consumer,
)


@pytest.mark.asyncio
class TestKafkaProducer:
    """Tests for Kafka producer."""

    @pytest.fixture
    async def mock_producer(self):
        """Create mock Kafka producer."""
        producer = AsyncMock()
        producer.start = AsyncMock()
        producer.stop = AsyncMock()
        producer.send_and_wait = AsyncMock()
        return producer

    async def test_get_kafka_producer(self, mock_producer):
        """Test getting Kafka producer instance."""
        with patch("blue_lantern.connectors.kafka_producer.AIOKafkaProducer", return_value=mock_producer):
            producer = get_kafka_producer()
            assert producer is not None
            await producer.start.assert_called_once()

    async def test_publish_alert(self, mock_producer):
        """Test publishing alert to Kafka."""
        alert = {
            "id": "ALT-001",
            "timestamp": "2026-04-25T14:32:00Z",
            "hostname": "DC-FINANCE-01",
            "rule_name": "Suspicious PowerShell",
        }
        source = "splunk"

        with patch("blue_lantern.connectors.kafka_producer.get_kafka_producer", return_value=mock_producer):
            success = await publish_alert(alert, source)
            assert success is True
            mock_producer.send_and_wait.assert_called_once()

    async def test_publish_alert_failure(self, mock_producer):
        """Test publishing alert failure."""
        alert = {
            "id": "ALT-001",
            "timestamp": "2026-04-25T14:32:00Z",
            "hostname": "DC-FINANCE-01",
            "rule_name": "Suspicious PowerShell",
        }
        source = "splunk"

        mock_producer.send_and_wait.side_effect = Exception("Kafka error")

        with patch("blue_lantern.connectors.kafka_producer.get_kafka_producer", return_value=mock_producer):
            success = await publish_alert(alert, source)
            assert success is False

    async def test_shutdown_kafka_producer(self, mock_producer):
        """Test shutting down Kafka producer."""
        with patch("blue_lantern.connectors.kafka_producer._producer", mock_producer):
            await shutdown_kafka_producer()
            mock_producer.stop.assert_called_once()


@pytest.mark.asyncio
class TestKafkaConsumer:
    """Tests for Kafka consumer."""

    @pytest.fixture
    async def mock_consumer(self):
        """Create mock Kafka consumer."""
        consumer = AsyncMock()
        consumer.start = AsyncMock()
        consumer.stop = AsyncMock()
        consumer.subscribe = AsyncMock()
        consumer.commit = AsyncMock()
        return consumer

    async def test_get_kafka_consumer(self, mock_consumer):
        """Test getting Kafka consumer instance."""
        with patch("blue_lantern.connectors.kafka_consumer.AIOKafkaConsumer", return_value=mock_consumer):
            consumer = get_kafka_consumer()
            assert consumer is not None
            await consumer.start.assert_called_once()

    async def test_start_kafka_consumer(self, mock_consumer):
        """Test starting Kafka consumer."""
        with patch("blue_lantern.connectors.kafka_consumer.get_kafka_consumer", return_value=mock_consumer):
            with patch("blue_lantern.connectors.kafka_consumer._running", False):
                await start_kafka_consumer()
                mock_consumer.subscribe.assert_called_once()

    async def test_stop_kafka_consumer(self, mock_consumer):
        """Test stopping Kafka consumer."""
        with patch("blue_lantern.connectors.kafka_consumer._consumer", mock_consumer):
            with patch("blue_lantern.connectors.kafka_consumer._running", True):
                await stop_kafka_consumer()
                mock_consumer.stop.assert_called_once()
