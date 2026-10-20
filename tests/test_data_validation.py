from datetime import datetime, timedelta, timezone

from data.loaders import DataFormatError, load_csv, load_json, load_manual
from data.models import Candle
from data.validation import validate_candles

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(i, o=10.0, h=10.1, l=9.9, c=10.0, step=120):
    return Candle(timestamp=T0 + timedelta(seconds=step * i), open=o, high=h, low=l, close=c)


def test_valid_series_passes():
    candles = [_candle(i) for i in range(60)]
    result = validate_candles(candles, timeframe_seconds=120, min_candles=50)
    assert result.ok


def test_insufficient_candles_flagged():
    candles = [_candle(i) for i in range(5)]
    result = validate_candles(candles, timeframe_seconds=120, min_candles=50)
    assert not result.ok
    assert any(e.code == "INSUFFICIENT_CANDLES" for e in result.errors)


def test_duplicate_timestamp_flagged():
    candles = [_candle(0), _candle(0), _candle(1)]
    result = validate_candles(candles, timeframe_seconds=120, min_candles=1)
    assert not result.ok
    assert any(e.code == "DUPLICATE_CANDLE" for e in result.errors)


def test_invalid_ohlc_high_below_low_flagged():
    bad = Candle(timestamp=T0, open=10, high=9, low=9.5, close=9.8)
    result = validate_candles([bad, _candle(1)], timeframe_seconds=120, min_candles=1)
    assert not result.ok
    assert any(e.code == "INVALID_OHLC" for e in result.errors)


def test_invalid_ohlc_close_above_high_flagged():
    bad = Candle(timestamp=T0, open=10, high=10.2, low=9.8, close=10.5)
    result = validate_candles([bad, _candle(1)], timeframe_seconds=120, min_candles=1)
    assert not result.ok
    assert any(e.code == "INVALID_OHLC" for e in result.errors)


def test_missing_candle_gap_is_warning_not_error():
    candles = [_candle(0), _candle(1), _candle(5)]  # big gap between index 1 and 2
    result = validate_candles(candles, timeframe_seconds=120, min_candles=1)
    assert any(w.code == "MISSING_CANDLES" for w in result.warnings)
    assert result.ok  # a gap alone is a warning, not fatal


def test_out_of_order_timestamp_flagged():
    c0 = _candle(1)
    c1 = _candle(0)  # earlier timestamp placed after
    result = validate_candles([c0, c1], timeframe_seconds=120, min_candles=1)
    assert not result.ok
    assert any(e.code == "OUT_OF_ORDER" for e in result.errors)


def test_stale_data_flagged():
    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
    candles = [Candle(timestamp=old_time + timedelta(seconds=120 * i), open=1, high=1.1, low=0.9, close=1) for i in range(10)]
    result = validate_candles(
        candles, timeframe_seconds=120, min_candles=1, max_stale_seconds=3600,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert not result.ok
    assert any(e.code == "STALE_DATA" for e in result.errors)


def test_no_data_flagged():
    result = validate_candles([], timeframe_seconds=120)
    assert not result.ok
    assert result.errors[0].code == "NO_DATA"


def test_negative_volume_flagged():
    bad = Candle(timestamp=T0, open=10, high=10.2, low=9.8, close=10, volume=-5)
    result = validate_candles([bad, _candle(1)], timeframe_seconds=120, min_candles=1)
    assert not result.ok
    assert any(e.code == "INVALID_VOLUME" for e in result.errors)


# --- Loaders -------------------------------------------------------------
def test_load_csv_basic():
    csv_text = "timestamp,open,high,low,close,volume\n2026-01-01T00:00:00Z,1,1.1,0.9,1.05,100\n2026-01-01T00:02:00Z,1.05,1.2,1.0,1.1,120\n"
    candles = load_csv(csv_text)
    assert len(candles) == 2
    assert candles[0].close == 1.05


def test_load_csv_missing_column_raises():
    csv_text = "timestamp,open,high,close\n2026-01-01T00:00:00Z,1,1.1,1.05\n"
    try:
        load_csv(csv_text)
        assert False, "expected DataFormatError"
    except DataFormatError:
        pass


def test_load_json_array():
    payload = [
        {"timestamp": "2026-01-01T00:00:00Z", "open": 1, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 10},
    ]
    candles = load_json(payload)
    assert len(candles) == 1


def test_load_manual_sorts_by_timestamp():
    rows = [
        {"timestamp": "2026-01-01T00:02:00Z", "open": 1, "high": 1.1, "low": 0.9, "close": 1.0},
        {"timestamp": "2026-01-01T00:00:00Z", "open": 1, "high": 1.1, "low": 0.9, "close": 1.0},
    ]
    candles = load_manual(rows)
    assert candles[0].timestamp < candles[1].timestamp


def test_load_manual_missing_required_field_raises():
    rows = [{"timestamp": "2026-01-01T00:00:00Z", "open": 1, "high": 1.1, "close": 1.0}]  # no low
    try:
        load_manual(rows)
        assert False, "expected DataFormatError"
    except DataFormatError:
        pass


def test_load_manual_invalid_numeric_raises():
    rows = [{"timestamp": "2026-01-01T00:00:00Z", "open": "abc", "high": 1.1, "low": 0.9, "close": 1.0}]
    try:
        load_manual(rows)
        assert False, "expected DataFormatError"
    except DataFormatError:
        pass
