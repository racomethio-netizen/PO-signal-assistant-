"""Market data validation.

Nothing downstream (indicators, scoring, signals) is allowed to run on data
that hasn't passed through here first. The validator never repairs or
invents data — it only reports what is wrong so the caller can refuse to
produce a signal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Sequence

from .models import Candle, IssueSeverity, ValidationIssue, ValidationResult

MIN_CANDLES_DEFAULT = 50
# How much slack (as a fraction of the timeframe) before a gap counts as a
# missing candle, to tolerate normal minor timestamp jitter.
GAP_TOLERANCE_FRACTION = 1.5


def validate_candles(
    candles: Sequence[Candle],
    timeframe_seconds: int,
    *,
    min_candles: int = MIN_CANDLES_DEFAULT,
    max_stale_seconds: int | None = None,
    now: datetime | None = None,
) -> ValidationResult:
    """Validate a sequence of candles for a given timeframe.

    ``max_stale_seconds``: if provided, the most recent candle's timestamp
    must be no older than this many seconds relative to ``now`` (defaults to
    the current UTC time), otherwise the data is flagged stale.
    """
    issues: List[ValidationIssue] = []
    now = now or datetime.now(timezone.utc)

    if len(candles) == 0:
        issues.append(
            ValidationIssue("NO_DATA", IssueSeverity.ERROR, "No candles were provided.")
        )
        return ValidationResult(ok=False, issues=issues)

    if len(candles) < min_candles:
        issues.append(
            ValidationIssue(
                "INSUFFICIENT_CANDLES",
                IssueSeverity.ERROR,
                f"Only {len(candles)} candle(s) provided; at least {min_candles} are "
                "required for reliable indicator calculation.",
            )
        )

    seen_timestamps = set()
    prev: Candle | None = None
    for idx, c in enumerate(candles):
        # Invalid OHLC relationships.
        if c.high < c.low:
            issues.append(
                ValidationIssue(
                    "INVALID_OHLC",
                    IssueSeverity.ERROR,
                    f"Candle at index {idx} ({c.timestamp.isoformat()}): high < low.",
                    index=idx,
                )
            )
        if c.high < c.open or c.high < c.close:
            issues.append(
                ValidationIssue(
                    "INVALID_OHLC",
                    IssueSeverity.ERROR,
                    f"Candle at index {idx} ({c.timestamp.isoformat()}): high is below "
                    "open/close.",
                    index=idx,
                )
            )
        if c.low > c.open or c.low > c.close:
            issues.append(
                ValidationIssue(
                    "INVALID_OHLC",
                    IssueSeverity.ERROR,
                    f"Candle at index {idx} ({c.timestamp.isoformat()}): low is above "
                    "open/close.",
                    index=idx,
                )
            )
        if c.volume < 0:
            issues.append(
                ValidationIssue(
                    "INVALID_VOLUME",
                    IssueSeverity.ERROR,
                    f"Candle at index {idx} ({c.timestamp.isoformat()}): negative volume.",
                    index=idx,
                )
            )

        # Duplicate timestamps.
        if c.timestamp in seen_timestamps:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_CANDLE",
                    IssueSeverity.ERROR,
                    f"Duplicate candle timestamp at index {idx} ({c.timestamp.isoformat()}).",
                    index=idx,
                )
            )
        seen_timestamps.add(c.timestamp)

        # Ordering and gaps.
        if prev is not None:
            delta = (c.timestamp - prev.timestamp).total_seconds()
            if delta <= 0:
                issues.append(
                    ValidationIssue(
                        "OUT_OF_ORDER",
                        IssueSeverity.ERROR,
                        f"Candle at index {idx} ({c.timestamp.isoformat()}) is not strictly "
                        "after the previous candle.",
                        index=idx,
                    )
                )
            elif delta > timeframe_seconds * GAP_TOLERANCE_FRACTION:
                missing = int(round(delta / timeframe_seconds)) - 1
                issues.append(
                    ValidationIssue(
                        "MISSING_CANDLES",
                        IssueSeverity.WARNING,
                        f"Gap detected before index {idx} ({c.timestamp.isoformat()}): "
                        f"~{missing} candle(s) appear to be missing.",
                        index=idx,
                    )
                )
        prev = c

    # Staleness.
    if max_stale_seconds is not None:
        last = candles[-1]
        age = (now - last.timestamp).total_seconds()
        if age > max_stale_seconds:
            issues.append(
                ValidationIssue(
                    "STALE_DATA",
                    IssueSeverity.ERROR,
                    f"Most recent candle is {int(age)}s old, which exceeds the "
                    f"{max_stale_seconds}s staleness limit.",
                )
            )

    ok = not any(i.severity == IssueSeverity.ERROR for i in issues)
    return ValidationResult(ok=ok, issues=issues)
