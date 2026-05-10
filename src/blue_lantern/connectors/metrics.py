"""OpenTelemetry metrics for SIEM alert ingress."""

import logging
from typing import Optional

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader

logger = logging.getLogger("blue-lantern.connectors.metrics")

# Initialize metrics
try:
    metric_reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter(__name__)
except Exception as e:
    logger.warning(f"Failed to initialize OpenTelemetry metrics: {e}")
    meter = None


# Counter metrics
if meter:
    alerts_ingested_total = meter.create_counter(
        "blue_lantern_alerts_ingested_total",
        description="Total number of alerts ingested",
    )

    alerts_processed_total = meter.create_counter(
        "blue_lantern_alerts_processed_total",
        description="Total number of alerts processed",
    )

    alerts_dropped_total = meter.create_counter(
        "blue_lantern_alerts_dropped_total",
        description="Total number of alerts dropped",
    )

    alerts_dlq_total = meter.create_counter(
        "blue_lantern_alerts_dlq_total",
        description="Total number of alerts sent to DLQ",
    )

    backpressure_triggered = meter.create_counter(
        "blue_lantern_backpressure_triggered",
        description="Number of times backpressure was triggered",
    )

    backpressure_resumed = meter.create_counter(
        "blue_lantern_backpressure_resumed",
        description="Number of times backpressure was resumed",
    )

    # Gauge metrics
    alert_queue_depth = meter.create_gauge(
        "blue_lantern_alert_queue_depth",
        description="Current depth of alert queue",
    )

    dlq_queue_depth = meter.create_gauge(
        "blue_lantern_dlq_queue_depth",
        description="Current depth of DLQ",
    )

    consumer_paused = meter.create_gauge(
        "blue_lantern_consumer_paused",
        description="Whether consumer is paused (1) or running (0)",
    )

    # Histogram metrics
    processing_latency = meter.create_histogram(
        "blue_lantern_processing_latency_seconds",
        description="Time to process an alert",
    )

    ingestion_to_triage_latency = meter.create_histogram(
        "blue_lantern_ingestion_to_triage_latency_seconds",
        description="Time from ingestion to triage start",
    )

    # Kafka metrics
    kafka_messages_published_total = meter.create_counter(
        "blue_lantern_kafka_messages_published_total",
        description="Total number of messages published to Kafka",
    )

    kafka_messages_consumed_total = meter.create_counter(
        "blue_lantern_kafka_messages_consumed_total",
        description="Total number of messages consumed from Kafka",
    )

    kafka_publish_errors_total = meter.create_counter(
        "blue_lantern_kafka_publish_errors_total",
        description="Total number of Kafka publish errors",
    )

    kafka_consumer_lag = meter.create_gauge(
        "blue_lantern_kafka_consumer_lag",
        description="Current consumer lag (messages behind)",
    )

    kafka_consumer_offset = meter.create_gauge(
        "blue_lantern_kafka_consumer_offset",
        description="Current consumer offset",
    )

    # GCP metrics
    gcp_uploads_total = meter.create_counter(
        "blue_lantern_gcp_uploads_total",
        description="Total number of uploads to GCP",
    )

    gcp_upload_errors_total = meter.create_counter(
        "blue_lantern_gcp_upload_errors_total",
        description="Total number of GCP upload errors",
    )

    gcp_upload_latency = meter.create_histogram(
        "blue_lantern_gcp_upload_latency_seconds",
        description="Time to upload to GCP",
    )

else:
    # Fallback no-op metrics
    alerts_ingested_total = None
    alerts_processed_total = None
    alerts_dropped_total = None
    alerts_dlq_total = None
    backpressure_triggered = None
    backpressure_resumed = None
    alert_queue_depth = None
    dlq_queue_depth = None
    consumer_paused = None
    processing_latency = None
    ingestion_to_triage_latency = None
    kafka_messages_published_total = None
    kafka_messages_consumed_total = None
    kafka_publish_errors_total = None
    kafka_consumer_lag = None
    kafka_consumer_offset = None
    gcp_uploads_total = None
    gcp_upload_errors_total = None
    gcp_upload_latency = None


def record_alert_ingested(source: str, alert_id: str):
    """Record alert ingestion metric."""
    if alerts_ingested_total:
        alerts_ingested_total.add(1, {"source": source, "alert_id": alert_id})


def record_alert_processed(severity: str):
    """Record alert processing metric."""
    if alerts_processed_total:
        alerts_processed_total.add(1, {"severity": severity})


def record_alert_dropped(reason: str):
    """Record alert drop metric."""
    if alerts_dropped_total:
        alerts_dropped_total.add(1, {"reason": reason})


def record_alert_dlq(error_type: str):
    """Record DLQ entry metric."""
    if alerts_dlq_total:
        alerts_dlq_total.add(1, {"error_type": error_type})


def record_backpressure_triggered():
    """Record backpressure trigger metric."""
    if backpressure_triggered:
        backpressure_triggered.add(1)


def record_backpressure_resumed():
    """Record backpressure resume metric."""
    if backpressure_resumed:
        backpressure_resumed.add(1)


def set_queue_depth(depth: int):
    """Set queue depth metric."""
    if alert_queue_depth:
        alert_queue_depth.set(depth)


def set_dlq_depth(depth: int):
    """Set DLQ depth metric."""
    if dlq_queue_depth:
        dlq_queue_depth.set(depth)


def set_consumer_paused(paused: bool):
    """Set consumer paused metric."""
    if consumer_paused:
        consumer_paused.set(1 if paused else 0)


def record_processing_latency(duration: float):
    """Record processing latency metric."""
    if processing_latency:
        processing_latency.record(duration)


def record_ingestion_to_triage_latency(duration: float):
    """Record ingestion to triage latency metric."""
    if ingestion_to_triage_latency:
        ingestion_to_triage_latency.record(duration)


def record_kafka_message_published(topic: str):
    """Record Kafka message published metric."""
    if kafka_messages_published_total:
        kafka_messages_published_total.add(1, {"topic": topic})


def record_kafka_message_consumed(topic: str):
    """Record Kafka message consumed metric."""
    if kafka_messages_consumed_total:
        kafka_messages_consumed_total.add(1, {"topic": topic})


def record_kafka_publish_error(topic: str, error: str):
    """Record Kafka publish error metric."""
    if kafka_publish_errors_total:
        kafka_publish_errors_total.add(1, {"topic": topic, "error": error})


def set_kafka_consumer_lag(lag: int):
    """Set Kafka consumer lag metric."""
    if kafka_consumer_lag:
        kafka_consumer_lag.set(lag)


def set_kafka_consumer_offset(offset: int):
    """Set Kafka consumer offset metric."""
    if kafka_consumer_offset:
        kafka_consumer_offset.set(offset)


def record_gcp_upload(bucket: str):
    """Record GCP upload metric."""
    if gcp_uploads_total:
        gcp_uploads_total.add(1, {"bucket": bucket})


def record_gcp_upload_error(bucket: str, error: str):
    """Record GCP upload error metric."""
    if gcp_upload_errors_total:
        gcp_upload_errors_total.add(1, {"bucket": bucket, "error": error})


def record_gcp_upload_latency(duration: float):
    """Record GCP upload latency metric."""
    if gcp_upload_latency:
        gcp_upload_latency.record(duration)
