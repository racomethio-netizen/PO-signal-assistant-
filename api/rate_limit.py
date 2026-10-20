"""A minimal in-memory sliding-window rate limiter for expensive endpoints
(signal generation, backtesting, LLM-backed calls). Not distributed —
fine for a single-process analysis assistant; swap for a shared store if
this is ever run with multiple worker processes.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {self.max_requests} requests per "
                f"{self.window_seconds:.0f}s for this endpoint.",
            )
        bucket.append(now)


def client_key(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"
