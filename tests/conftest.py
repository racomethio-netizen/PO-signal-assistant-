"""Deterministic fixtures shared across the test suite.

No fixture here uses real randomness without a fixed seed — every test
that depends on synthetic candle data must be fully repeatable.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.models import Candle

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def make_candle(i: int, o: float, h: float, l: float, c: float, v: float = 100.0, step_seconds: int = 120) -> Candle:
    return Candle(timestamp=T0 + timedelta(seconds=step_seconds * i), open=o, high=h, low=l, close=c, volume=v)


def deterministic_series(
    n: int,
    start_price: float = 100.0,
    drift: float = 0.0,
    amplitude: float = 0.5,
    step_seconds: int = 120,
) -> List[Candle]:
    """A fully deterministic (no RNG) oscillating + drifting price series,
    built from a sine-like pattern using only arithmetic, so results are
    reproducible across runs/machines without depending on `random`."""
    candles = []
    price = start_price
    for i in range(n):
        # deterministic pseudo-oscillation via a simple triangular wave
        phase = i % 10
        wobble = (phase - 5) / 5.0 * amplitude
        o = price
        c = price + drift + wobble
        h = max(o, c) + abs(amplitude) * 0.2
        l = min(o, c) - abs(amplitude) * 0.2
        candles.append(make_candle(i, o, h, l, c, step_seconds=step_seconds))
        price = c
    return candles


@pytest.fixture
def uptrend_candles() -> List[Candle]:
    return deterministic_series(120, start_price=100.0, drift=0.3, amplitude=0.4)


@pytest.fixture
def downtrend_candles() -> List[Candle]:
    return deterministic_series(120, start_price=100.0, drift=-0.3, amplitude=0.4)


@pytest.fixture
def flat_candles() -> List[Candle]:
    candles = []
    price = 50.0
    for i in range(100):
        wobble = 0.001 if i % 2 == 0 else -0.001
        o = price
        c = price + wobble
        h = max(o, c) + 0.0005
        l = min(o, c) - 0.0005
        candles.append(make_candle(i, o, h, l, c, step_seconds=180))
        price = c
    return candles


@pytest.fixture
def tiny_closes() -> List[float]:
    """A short, hand-checkable close-price series for indicator math tests."""
    return [10, 10.5, 10.2, 10.8, 11.0, 10.9, 11.3, 11.5, 11.2, 11.8, 12.0, 11.7, 12.3, 12.5, 12.1]
