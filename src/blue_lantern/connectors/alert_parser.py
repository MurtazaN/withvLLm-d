"""
Alert blob parser.

Pure file-format parsing — turns the bytes/text of a GCS object into a
list of raw alert dicts. Does NOT know anything about the Blue Lantern
``Alert`` schema or per-vendor field layouts; that's the mapper layer's
job (see ``siem_*.py`` and ``Alert``'s pre-validator in ``schemas.py``).

Supported formats, dispatched by filename suffix:

  * ``.json``   — single JSON document. Top-level may be one alert
                  (object) or a list of alerts.
  * ``.jsonl``  — newline-delimited JSON, one alert per non-empty line.
                  Malformed lines are logged and skipped.
  * ``.csv``    — RFC-4180 CSV with a header row. Every data row becomes
                  one alert dict keyed by column name. Cells stay as
                  strings; downstream mappers cast as needed.

Anything else returns ``[]`` and logs at error level.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Iterable

logger = logging.getLogger("blue-lantern.alert_parser")

SUPPORTED_SUFFIXES: tuple[str, ...] = (".json", ".jsonl", ".csv")


def parse_blob(content: str, blob_name: str) -> list[dict]:
    """Parse a blob's text body into a list of raw alert dicts.

    Args:
        content: Decoded blob body.
        blob_name: GCS object name. Suffix selects the parser.

    Returns:
        A (possibly empty) list of dicts. Never raises; parser errors
        are logged and the caller gets whatever was salvageable.
    """
    if not content or not content.strip():
        return []

    name = blob_name.lower()
    if name.endswith(".jsonl"):
        return _parse_jsonl(content, blob_name)
    if name.endswith(".csv"):
        return _parse_csv(content, blob_name)
    if name.endswith(".json"):
        return _parse_json(content, blob_name)

    logger.error(
        f"Unsupported blob suffix for {blob_name}; "
        f"expected one of {SUPPORTED_SUFFIXES}"
    )
    return []


# ── Format-specific helpers ──────────────────────────────────────────


def _parse_jsonl(content: str, blob_name: str) -> list[dict]:
    """One alert per non-empty line. Skip & log malformed lines."""
    alerts: list[dict] = []
    for line_no, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            logger.error(
                f"Skipping malformed JSONL line {blob_name}:{line_no}: {e}"
            )
            continue
        if isinstance(obj, dict):
            alerts.append(obj)
        else:
            logger.error(
                f"Skipping non-object JSONL line {blob_name}:{line_no}: "
                f"got {type(obj).__name__}"
            )
    return alerts


def _parse_json(content: str, blob_name: str) -> list[dict]:
    """Single JSON: either an object (one alert) or a list of objects."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse {blob_name} as JSON: {e}")
        return []

    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return _filter_dicts(parsed, blob_name)
    logger.error(
        f"Unexpected JSON shape in {blob_name}: {type(parsed).__name__}"
    )
    return []


def _parse_csv(content: str, blob_name: str) -> list[dict]:
    """Header-rowed CSV. Each data row becomes one dict."""
    try:
        reader = csv.DictReader(io.StringIO(content))
        if reader.fieldnames is None:
            logger.error(f"CSV {blob_name} has no header row")
            return []
        # csv.DictReader yields plain dicts of {col_name: cell_str}.
        # Cells stay as strings; mappers handle type coercion.
        return [
            {k: v for k, v in row.items() if k is not None}
            for row in reader
            if row  # skip blank lines
        ]
    except csv.Error as e:
        logger.error(f"Failed to parse {blob_name} as CSV: {e}")
        return []


def _filter_dicts(items: Iterable, blob_name: str) -> list[dict]:
    """Keep only dict items in a JSON list; log non-dict elements."""
    out: list[dict] = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            out.append(item)
        else:
            logger.error(
                f"Skipping non-object element in {blob_name}[{i}]: "
                f"{type(item).__name__}"
            )
    return out
