"""A minimal in-process TTL cache for the FastAPI layer.

The reporting endpoints sit on top of an engine pipeline that is genuinely
expensive to re-run per request: `ScoringEngine.run()` reads several SQL
tables per factor, `construct_portfolio()`'s MVO mode solves a cvxpy convex
program, and `evaluate_portfolio()`'s stress-test check makes live yfinance
calls for each of the three historical crash windows. None of that should
re-run on every dashboard page load, so results are cached here with a TTL
and can be force-refreshed via each endpoint's `refresh` query param.

This is intentionally not a general-purpose caching library (no LRU
eviction, no size bound) — the key space is small (one entry per distinct
ticker-universe requested) and the process is a single dev/research
server, not a multi-worker production deployment.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Callable, TypeVar

T = TypeVar("T")


class TTLCache:
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = Lock()

    def get_or_compute(self, key: str, compute: Callable[[], T]) -> T:
        with self._lock:
            hit = self._store.get(key)
            if hit is not None:
                stored_at, value = hit
                if time.monotonic() - stored_at < self.ttl_seconds:
                    return value  # type: ignore[return-value]

        value = compute()
        with self._lock:
            self._store[key] = (time.monotonic(), value)
        return value

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)
