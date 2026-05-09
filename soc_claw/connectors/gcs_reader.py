"""
GCS Reader Module

Reads SIEM alert files from a GCS bucket. Objects are expected to be
either a single JSON document or a newline-delimited JSONL batch with
one alert per line. Non-JSON objects (README, etc.) are filtered out
by extension.
"""

import json
import logging
from typing import Optional

from google.cloud import storage
from google.cloud.storage import Client

logger = logging.getLogger("soc-claw.gcs_reader")

# Blobs we'll attempt to parse. Anything else (README.md, .txt, ...) is
# skipped client-side rather than spamming the log with parse errors.
_ALERT_SUFFIXES = (".jsonl", ".json")


def get_gcs_client() -> Optional[Client]:
    """Get GCS client using Application Default Credentials."""
    try:
        return storage.Client()
    except Exception as e:
        logger.error(f"Failed to create GCS client: {e}")
        return None


def _parse_blob(content: str, object_name: str) -> list[dict]:
    """Parse a blob's body as either a single JSON or JSONL.

    Returns the list of alerts found, or [] on any parse failure.
    """
    stripped = content.strip()
    if not stripped:
        return []

    # JSONL: one alert per non-empty line.
    if object_name.endswith(".jsonl"):
        out: list[dict] = []
        for line_no, line in enumerate(stripped.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.error(
                    f"Skipping malformed JSONL line in {object_name}:{line_no}: {e}"
                )
        return out

    # Single JSON document. Could be one alert or a list.
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse {object_name} as JSON: {e}")
        return []

    if isinstance(parsed, list):
        return [a for a in parsed if isinstance(a, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    logger.error(f"Unexpected JSON shape in {object_name}: {type(parsed).__name__}")
    return []


def list_alerts(bucket_name: str, max_results: int = 30) -> list[dict]:
    """List the most recently created alert blobs in a bucket.

    Returns object metadata (`name`, `updated`, `size`) for blobs whose
    name ends in `.json`/`.jsonl`. The GCS list API has no time-based
    ordering, so we scan the page and sort client-side. For large
    buckets, partition the layout by date prefix and pass `prefix=` to
    narrow the scan.
    """
    client = get_gcs_client()
    if not client:
        logger.error("GCS client not available")
        return []

    try:
        bucket = client.bucket(bucket_name)
        candidates = [
            b for b in bucket.list_blobs()
            if b.name.endswith(_ALERT_SUFFIXES)
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
    """Download a single alert by GCS object name.

    Only meaningful for blobs that hold a single JSON object. JSONL
    blobs return their *first* alert (callers that need every alert
    should use `download_batch`). Returns None if the blob is missing,
    unparseable, or empty.
    """
    client = get_gcs_client()
    if not client:
        logger.error("GCS client not available")
        return None

    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        content = blob.download_as_text()
    except Exception as e:
        logger.error(
            f"Failed to download alert {object_name} from GCS bucket {bucket_name}: {e}"
        )
        return None

    alerts = _parse_blob(content, object_name)
    return alerts[0] if alerts else None


def download_batch(bucket_name: str, max_results: int = 30) -> list[dict]:
    """Download and flatten alerts from the most recent blobs.

    Walks the most recent JSON/JSONL blobs in `bucket_name` and returns
    up to `max_results` alerts in total (newer blobs first, line order
    within each blob). Stops early once the cap is reached.
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
        alerts.extend(_parse_blob(content, obj["name"]))

    alerts = alerts[:max_results]
    logger.info(
        f"Downloaded {len(alerts)} alerts from {len(objects)} blobs "
        f"in GCS bucket {bucket_name}"
    )
    return alerts
