"""Loaders that turn raw user-supplied input into ``Candle`` objects.

Supported inputs: CSV upload, JSON OHLCV, and manual candle entry (a plain
list of dicts, e.g. typed into the dashboard). A fourth adapter interface
for an authorized live market-data source is defined in
``data/adapter.py`` but is NOT implemented here — see that module's
docstring. No loader ever fabricates a missing field.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from .models import Candle

REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


class DataFormatError(ValueError):
    """Raised when input data is missing required fields or malformed."""


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        # Accept unix seconds or milliseconds.
        ts = float(value)
        if ts > 10_000_000_000:  # looks like milliseconds
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.replace(".", "", 1).isdigit():
            return _parse_timestamp(float(text))
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError as exc:
            raise DataFormatError(f"Unrecognized timestamp format: {value!r}") from exc
    raise DataFormatError(f"Unrecognized timestamp type: {type(value)!r}")


def _row_to_candle(row: Dict[str, Any], index: int) -> Candle:
    missing = [f for f in REQUIRED_FIELDS if f not in row or row[f] in (None, "")]
    # volume is allowed to default to 0 if truly absent from the source,
    # but every other field is mandatory.
    missing = [f for f in missing if f != "volume"]
    if missing:
        raise DataFormatError(
            f"Row {index}: missing required field(s): {', '.join(missing)}"
        )
    try:
        return Candle(
            timestamp=_parse_timestamp(row["timestamp"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0.0) or 0.0),
        )
    except (TypeError, ValueError) as exc:
        raise DataFormatError(f"Row {index}: invalid numeric value ({exc}).") from exc


def load_manual(rows: Iterable[Dict[str, Any]]) -> List[Candle]:
    """Build candles from a list of plain dicts (manual entry)."""
    candles = [_row_to_candle(row, i) for i, row in enumerate(rows)]
    return sorted(candles, key=lambda c: c.timestamp)


def load_json(payload: str | bytes | List[Dict[str, Any]]) -> List[Candle]:
    """Load candles from a JSON OHLCV array.

    Accepts either a raw JSON string/bytes or an already-parsed list.
    """
    if isinstance(payload, (str, bytes)):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DataFormatError(f"Invalid JSON: {exc}") from exc
    else:
        data = payload

    if isinstance(data, dict) and "candles" in data:
        data = data["candles"]
    if not isinstance(data, list):
        raise DataFormatError("Expected a JSON array of OHLCV objects.")

    return load_manual(data)


def load_csv(payload: str | bytes) -> List[Candle]:
    """Load candles from CSV text with a header row containing
    timestamp,open,high,low,close,volume (any column order; volume optional).
    """
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(payload))
    if reader.fieldnames is None:
        raise DataFormatError("CSV has no header row.")
    normalized_fields = {f.strip().lower(): f for f in reader.fieldnames}
    missing = [f for f in ("timestamp", "open", "high", "low", "close") if f not in normalized_fields]
    if missing:
        raise DataFormatError(f"CSV missing required column(s): {', '.join(missing)}")

    rows = []
    for raw_row in reader:
        row = {key: raw_row.get(normalized_fields[key]) for key in normalized_fields}
        rows.append(row)
    return load_manual(rows)
