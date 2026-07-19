import time
from collections import deque

MINUTE_SECONDS = 60.0
DAY_SECONDS = 86400.0


class DailyQuotaExceededError(RuntimeError):
    """Raised (never slept through) once the daily request budget is hit.

    Sleeping until the quota resets could mean blocking for up to 24h, which
    would silently stall a batch job overnight. Failing loud instead lets the
    caller stop and resume tomorrow — cached results (ai_analysis_cache) mean
    nothing already-analyzed gets re-done.
    """


class RateLimiter:
    """Proactive sliding-window throttle for Gemini calls.

    This is a *supplement* to GeminiClient's existing reactive 429 backoff,
    not a replacement: throttling before the call avoids most 429s outright,
    which matters because a burst that trips the daily/minute cap can take a
    while for the reactive backoff to recover from. `time_fn` is injectable
    so tests can drive it with a fake clock instead of sleeping for real.
    """

    def __init__(
        self,
        requests_per_minute: int,
        requests_per_day: int,
        time_fn=time.monotonic,
        sleep_fn=time.sleep,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_day = requests_per_day
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._minute_window: deque[float] = deque()
        self._day_window: deque[float] = deque()

    def _evict(self, window: deque[float], now: float, span_seconds: float) -> None:
        while window and now - window[0] >= span_seconds:
            window.popleft()

    def acquire(self) -> None:
        now = self._time_fn()
        self._evict(self._minute_window, now, MINUTE_SECONDS)
        self._evict(self._day_window, now, DAY_SECONDS)

        if len(self._minute_window) >= self.requests_per_minute:
            sleep_for = MINUTE_SECONDS - (now - self._minute_window[0])
            if sleep_for > 0:
                self._sleep_fn(sleep_for)
            now = self._time_fn()
            self._evict(self._minute_window, now, MINUTE_SECONDS)

        if len(self._day_window) >= self.requests_per_day:
            raise DailyQuotaExceededError(
                f"Hit the daily Gemini request budget ({self.requests_per_day}/day). "
                "Resume the batch after the quota resets — already-analyzed items "
                "are cached and won't be re-sent."
            )

        now = self._time_fn()
        self._minute_window.append(now)
        self._day_window.append(now)
