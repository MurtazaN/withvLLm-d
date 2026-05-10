"""
GCS Reader Module.

Lists and downloads alert blobs from a GCS bucket. Format parsing is
delegated to ``alert_parser.parse_blob`` so JSON / JSONL / CSV layouts
all flow through the same code path.
"""

import logging
from typing import Optional

from google.cloud import storage
from google.cloud.storage import Client

from pydantic import ValidationError

from blue_lantern.connectors.alert_parser import SUPPORTED_SUFFIXES, parse_blob
from blue_lantern.schemas import Alert

logger = logging.getLogger("blue-lantern.gcs_reader")


def _normalize(raw: dict, blob_name: str) -> dict | None:
    """Validate + project a raw alert dict via the ``Alert`` schema.

    The Alert model's ``mode='before'`` validator handles the synthetic
    dataset's field mapping (``event_id`` → ``id``, severity coercion,
    etc.). Rows that fail validation outright (e.g., missing both
    ``id``/``event_id`` or ``timestamp``) are logged and skipped so a
    single bad blob doesn't take down the whole batch.
    """
    try:
        return Alert.model_validate(raw).model_dump()
    except ValidationError as e:
        logger.error(f"Skipping unmappable alert in {blob_name}: {e}")
        return None


def get_gcs_client() -> Optional[Client]:
    """Get GCS client using Application Default Credentials."""
    try:
        return storage.Client()
    except Exception as e:
        logger.error(f"Failed to create GCS client: {e}")
        return None


def list_alerts(bucket_name: str, max_results: int = 30) -> list[dict]:
    """List the most recently created alert blobs in a bucket.

    Returns object metadata (`name`, `updated`, `size`) for blobs whose
    name ends in a supported suffix. The GCS list API has no time-based
    ordering, so we scan and sort client-side. For large buckets,
    partition the layout by date prefix and pass `prefix=` to narrow
    the scan.
    """
    client = get_gcs_client()
    if not client:
        logger.error("GCS client not available")
        return []

    try:
        bucket = client.bucket(bucket_name)
        candidates = [
            b for b in bucket.list_blobs()
            if b.name.lower().endswith(SUPPORTED_SUFFIXES)
        ]
        candidates.sort(key=lambda b: b.time_created, reverse=True)
        candidates = candidates[:max_results]
        return [
            {"name": b.name, "updated": b.updated.isoformat(), "size": b.size}
            for b in candidates
        ]
    except Exception as e:
        logger.error(f"Failed to list alerts from GCS bucket {bucket_name}: {e}")
        return []


def download_alert(bucket_name: str, object_name: str) -> Optional[dict]:
    """Download a single blob and return its first alert.

    Useful for blobs that hold a single alert. JSONL / multi-row CSV
    blobs return their first alert (callers needing every alert should
    use ``download_batch``). Returns None if the blob is missing,
    unparseable, or empty.
    """
    client = get_gcs_client()
    if not client:
        logger.error("GCS client not available")
        return None

    try:
        bucket = client.bucket(bucket_name)
        content = bucket.blob(object_name).download_as_text()
    except Exception as e:
        logger.error(
            f"Failed to download alert {object_name} from GCS bucket {bucket_name}: {e}"
        )
        return None

    parsed = parse_blob(content, object_name)
    for alert in parsed:
        normalized = _normalize(alert, object_name)
        if normalized is not None:
            return normalized
    return None


def download_batch(bucket_name: str, max_results: int = 30) -> list[dict]:
    """Download and flatten alerts from the most recent blobs.

    Walks the most recent supported blobs in `bucket_name` and returns
    up to `max_results` alerts in total (newer blobs first, original
    order within each blob). Stops early once the cap is reached.
    """
    # Pull metadata for more blobs than the alert cap, since each blob
    # may carry many or zero alerts after filtering. Bounded so we
    # don't list the entire bucket on huge deployments.
    object_scan_cap = max(max_results * 4, max_results + 16)
    objects = list_alerts(bucket_name, max_results=object_scan_cap)

    client = get_gcs_client()
    if not client:
        return []

    bucket = client.bucket(bucket_name)
    alerts: list[dict] = []
    for obj in objects:
        if len(alerts) >= max_results:
            break
        try:
            content = bucket.blob(obj["name"]).download_as_text()
        except Exception as e:
            logger.error(f"Failed to download {obj['name']}: {e}")
            continue
        for raw in parse_blob(content, obj["name"]):
            if len(alerts) >= max_results:
                break
            normalized = _normalize(raw, obj["name"])
            if normalized is not None:
                alerts.append(normalized)

    alerts = alerts[:max_results]
    logger.info(
        f"Downloaded {len(alerts)} alerts from {len(objects)} blobs "
        f"in GCS bucket {bucket_name}"
    )
    return alerts
